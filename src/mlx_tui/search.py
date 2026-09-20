"""HF search + snapshot download."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override
from urllib.parse import urlparse

from huggingface_hub import snapshot_download
from huggingface_hub.constants import HF_HUB_CACHE
from huggingface_hub.hf_api import ModelInfo
from huggingface_hub.utils import filter_repo_objects
from huggingface_hub.utils import tqdm as hf_tqdm

# Supported MLX-LM loader file set. Note: *.py pulls executable custom code,
# matching the loader contract.
ALLOW_PATTERNS = [
    "*.safetensors",
    "*.json",
    "tokenizer*",
    "*.py",
    "*.tiktoken",
    "tiktoken.model",
    "*.txt",
    "*.jsonl",
    "*.jinja",
]
_SEARCH_LIMIT = 50
_PROGRESS_MIN_INTERVAL_S = 0.5


class HubApi(Protocol):
    def list_models(
        self,
        *,
        search: str | None,
        filter: str,
        sort: str,
        limit: int,
        author: str | None = None,
    ) -> Iterable[ModelInfo]: ...

    def model_info(
        self, repo_id: str, *, files_metadata: bool, revision: str | None = None
    ) -> ModelInfo: ...


class CancelledDownload(Exception):
    pass


def exact_repo_id(query: str) -> str | None:
    """Accept a Hub model URL or an exact owner/repository identifier."""
    value = query.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        if parsed.hostname not in {"huggingface.co", "www.huggingface.co"}:
            return None
        value = parsed.path.strip("/")
        if value.startswith("models/"):
            value = value.removeprefix("models/")
        value = "/".join(value.split("/")[:2])
    parts = value.split("/")
    if len(parts) == 2 and all(  # noqa: PLR2004
        part
        and part not in {".", ".."}
        and all(c.isalnum() or c in "-_." for c in part)
        for part in parts
    ):
        return value
    return None


def list_results(
    api: HubApi, query: str, *, limit: int = _SEARCH_LIMIT, author: str | None = None
) -> list[str]:
    exact = exact_repo_id(query)
    if exact is not None:
        api.model_info(exact, files_metadata=False)
        return [exact]
    if author:
        results = api.list_models(
            search=query or None,
            filter="mlx",
            sort="downloads",
            limit=limit,
            author=author,
        )
    else:
        results = api.list_models(
            search=query or None, filter="mlx", sort="downloads", limit=limit
        )
    return [info.id for info in results]


def filtered_download_size(files: Iterable[tuple[str, int | None]]) -> int | None:
    kept = filter_repo_objects(
        [{"path": name, "size": size} for name, size in files],
        allow_patterns=ALLOW_PATTERNS,
        key=lambda f: f["path"],
    )
    selected = {entry["path"]: entry["size"] for entry in kept}
    if any(size is None for size in selected.values()):
        return None
    return sum(size for size in selected.values() if size is not None)


@dataclass(frozen=True)
class RepoSnapshot:
    revision: str | None
    files: tuple[tuple[str, int | None], ...]


def repo_snapshot(
    api: HubApi, repo_id: str, *, revision: str | None = None
) -> RepoSnapshot:
    requested_revision = revision
    info = api.model_info(repo_id, files_metadata=True, revision=requested_revision)
    sha = getattr(info, "sha", None)
    resolved_revision = sha if isinstance(sha, str) else None
    if requested_revision is not None and sha != requested_revision:
        raise ValueError(
            f"Hub returned {sha!r}, expected pinned revision {requested_revision!r}"
        )
    siblings = info.siblings or []
    files = tuple((s.rfilename, s.size) for s in siblings)
    return RepoSnapshot(revision=resolved_revision, files=files)


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


def _throttled_tqdm(
    on_progress: Callable[[int, int], None] | None,
    cancel_event: threading.Event | None,
) -> type[hf_tqdm]:
    """Closure throttling 0.5s; single writer thread, no lock needed."""
    state: dict[str, float | int] = {
        "disk_bytes": 0,
        "expected": 0,
        "last_flush": float("-inf"),
    }

    def _desc(obj: object) -> str:
        return str(getattr(obj, "desc", getattr(obj, "_mlx_desc", "")) or "")  # type: ignore[attr-defined]

    class _Tqdm(hf_tqdm):
        @override
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._mlx_desc: str = str(
                kwargs.get("desc") or (str(args[0]) if args else "")
            )  # type: ignore[attr-defined]
            super().__init__(*args, **kwargs)
            if not hasattr(self, "desc"):
                object.__setattr__(self, "desc", self._mlx_desc)

        @override
        def update(self, n: float | None = 1) -> bool | None:
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledDownload("cancelled by user")
            if _desc(self).startswith("Downloading"):
                return super().update(n)
            now = time.monotonic()
            state["disk_bytes"] = int(state["disk_bytes"]) + max(int(n or 0), 0)
            state["expected"] = max(
                int(state["expected"]), int(getattr(self, "total", 0) or 0)
            )
            if (
                on_progress is None
                or now - float(state["last_flush"]) < _PROGRESS_MIN_INTERVAL_S
            ):
                return super().update(n)
            state["last_flush"] = now
            on_progress(state["disk_bytes"], state["expected"])
            return super().update(n)

        @override
        def close(self) -> None:
            if (
                not _desc(self).startswith("Downloading")
                and on_progress is not None
                and not (cancel_event is not None and cancel_event.is_set())
            ):
                on_progress(int(state["disk_bytes"]), int(state["expected"]))
            super().close()

    _Tqdm._mlx_state = state  # type: ignore[attr-defined]
    return _Tqdm


def download_snapshot(
    repo_id: str,
    *,
    cache_dir: str | Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    revision: str | None = None,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledDownload("cancelled by user")
    kwargs: dict[str, object] = {
        "allow_patterns": ALLOW_PATTERNS,
        "tqdm_class": _throttled_tqdm(on_progress, cancel_event),
        "cache_dir": cache_dir,
    }
    if revision is not None:
        kwargs["revision"] = revision
    snapshot_download(repo_id, **kwargs)  # type: ignore[arg-type]
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledDownload("cancelled by user")
