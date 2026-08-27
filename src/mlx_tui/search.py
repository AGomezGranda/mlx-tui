"""HF search + snapshot download."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, override

from huggingface_hub import snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE
from huggingface_hub.hf_api import ModelInfo
from huggingface_hub.utils import filter_repo_objects
from huggingface_hub.utils import tqdm as hf_tqdm

ALLOW_PATTERNS = ["*.safetensors", "*.json", "tokenizer*"]
_SEARCH_AUTHOR = "mlx-community"
_SEARCH_LIMIT = 50
_PROGRESS_MIN_INTERVAL_S = 0.5


class HubApi(Protocol):
    def list_models(
        self, *, author: str, search: str, limit: int
    ) -> Iterable[ModelInfo]: ...

    def model_info(self, repo_id: str, *, files_metadata: bool) -> ModelInfo: ...


class CancelledDownload(Exception):
    pass


def list_results(api: HubApi, query: str) -> list[str]:
    return [
        info.id
        for info in api.list_models(
            author=_SEARCH_AUTHOR, search=query, limit=_SEARCH_LIMIT
        )
    ]


def filtered_download_size(files: Iterable[tuple[str, int]]) -> int:
    kept = filter_repo_objects(
        [{"path": name, "size": size} for name, size in files],
        allow_patterns=ALLOW_PATTERNS,
        key=lambda f: f["path"],
    )
    return sum(entry["size"] for entry in kept)


def repo_files_with_sizes(api: HubApi, repo_id: str) -> list[tuple[str, int]]:
    info = api.model_info(repo_id, files_metadata=True)
    return [(s.rfilename, s.size or 0) for s in info.siblings or []]


def free_disk_bytes(cache_dir: Path | None = None) -> int | None:
    probe = Path(cache_dir) if cache_dir is not None else Path(HF_HUB_CACHE)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return None
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def fits_disk(size_bytes: int, free_bytes: int | None) -> bool | None:
    if free_bytes is None:
        return None
    return size_bytes < free_bytes


@dataclass
class DownloadProgress:
    cancel_event: threading.Event = field(default_factory=threading.Event)
    on_progress: Callable[[int, int], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _disk_bytes: int = 0
    _expected: int = 0
    _last_flush: float = float("-inf")

    def record(self, *, disk_delta: int, expected: int) -> tuple[int, int] | None:
        now = time.monotonic()
        with self._lock:
            self._disk_bytes += max(disk_delta, 0)
            self._expected = max(self._expected, expected)
            if (
                self.on_progress is None
                or now - self._last_flush < _PROGRESS_MIN_INTERVAL_S
            ):
                return None
            self._last_flush = now
            return (self._disk_bytes, self._expected)

    def totals(self) -> tuple[int, int]:
        with self._lock:
            return (self._disk_bytes, self._expected)


def make_progress_tqdm(progress: DownloadProgress) -> type[hf_tqdm]:
    """tqdm forwarding throttled totals; xet bar ignored for display."""

    class ProgressTqdm(hf_tqdm):
        @override
        def __init__(self, *args: object, **kwargs: object) -> None:
            desc = kwargs.get("desc", "")
            if args and not desc:
                desc = str(args[0]) if args else ""
            self._mlx_desc: str = str(desc)  # type: ignore[attr-defined]
            super().__init__(*args, **kwargs)
            if not hasattr(self, "desc"):
                object.__setattr__(self, "desc", self._mlx_desc)

        @override
        def update(self, n: float | None = 1) -> bool | None:
            if progress.cancel_event.is_set():
                raise CancelledDownload("cancelled by user")
            raw = getattr(self, "desc", getattr(self, "_mlx_desc", ""))  # type: ignore[attr-defined]
            desc = str(raw or "")
            if desc.startswith("Downloading"):
                return super().update(n)
            snapshot = progress.record(
                disk_delta=int(n or 0),
                expected=int(getattr(self, "total", 0) or 0),
            )
            if progress.on_progress is not None and snapshot is not None:
                progress.on_progress(*snapshot)
            return super().update(n)

        @override
        def close(self) -> None:
            raw = getattr(self, "desc", getattr(self, "_mlx_desc", ""))  # type: ignore[attr-defined]
            desc = str(raw or "")
            if (
                not desc.startswith("Downloading")
                and progress.on_progress is not None
                and not progress.cancel_event.is_set()
            ):
                progress.on_progress(*progress.totals())
            super().close()

    return ProgressTqdm


def download_snapshot(
    repo_id: str,
    *,
    cache_dir: str | Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    progress = DownloadProgress()
    if cancel_event is not None:
        progress.cancel_event = cancel_event
    progress.on_progress = on_progress
    snapshot_download(
        repo_id,
        allow_patterns=ALLOW_PATTERNS,
        tqdm_class=make_progress_tqdm(progress),
        cache_dir=cache_dir,
    )
