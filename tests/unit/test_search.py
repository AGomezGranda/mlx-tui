"""Unit tests for mlx_tui.search — pure HF domain, no UI."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import mlx_tui.search as search_mod
from mlx_tui.search import (
    ALLOW_PATTERNS,
    CancelledDownload,
    _throttled_tqdm,
    filtered_download_size,
    fits_disk,
    free_disk_bytes,
    list_results,
    repo_files_with_sizes,
)

# ---------------------------------------------------------------------------
# Stub helpers ( HubApi structural )
# ---------------------------------------------------------------------------


class _StubApi:
    """Minimal HubApi stub; records kwargs for assertions."""

    def __init__(
        self,
        *,
        list_infos: list[Any] | None = None,
        siblings: list[Any] | None = None,
    ) -> None:
        self.list_infos = list_infos or []
        self.siblings = siblings or []
        self.captured_list_kwargs: dict[str, object] | None = None
        self.captured_model_info: tuple[str, bool] | None = None

    def list_models(self, *, author: str, search: str, limit: int):  # type: ignore[no-untyped-def]
        self.captured_list_kwargs = {"author": author, "search": search, "limit": limit}
        return self.list_infos

    def model_info(self, repo_id: str, *, files_metadata: bool):  # type: ignore[no-untyped-def]
        self.captured_model_info = (repo_id, files_metadata)
        return SimpleNamespace(siblings=self.siblings)


def _siblings_with_sizes(*pairs: tuple[str, int | None]):  # type: ignore[no-untyped-def]
    return [SimpleNamespace(rfilename=name, size=size) for name, size in pairs]


# ---------------------------------------------------------------------------
# list_results
# ---------------------------------------------------------------------------


def test_list_results_maps_ids_and_pins_scope() -> None:
    from huggingface_hub.hf_api import ModelInfo  # noqa: PLC0415

    infos = [
        ModelInfo(id="mlx-community/foo-4bit"),
        ModelInfo(id="mlx-community/bar-8bit"),
    ]
    stub = _StubApi(list_infos=infos)
    ids = list_results(stub, "qwen")  # type: ignore[arg-type]
    assert ids == ["mlx-community/foo-4bit", "mlx-community/bar-8bit"]
    assert stub.captured_list_kwargs == {
        "author": "mlx-community",
        "search": "qwen",
        "limit": 50,
    }


# ---------------------------------------------------------------------------
# filtered_download_size
# ---------------------------------------------------------------------------


def test_filtered_download_size_matches_downloader() -> None:
    files = [
        ("model.safetensors", 100),
        ("README.md", 50),
        ("tokenizer.json", 10),
        ("config.json", 5),
        ("weights.gguf", 999),
    ]
    assert filtered_download_size(files) == 115
    nested = [*files, ("sub/w.safetensors", 7)]
    assert filtered_download_size(nested) == 122


# ---------------------------------------------------------------------------
# repo_files_with_sizes
# ---------------------------------------------------------------------------


def test_repo_files_with_sizes_maps_siblings() -> None:
    stub = _StubApi(
        siblings=_siblings_with_sizes(
            ("a.safetensors", 100), ("b.json", None), ("c.json", 50)
        )
    )
    pairs = repo_files_with_sizes(stub, "mlx-community/x")  # type: ignore[arg-type]
    assert pairs == [("a.safetensors", 100), ("b.json", 0), ("c.json", 50)]
    assert stub.captured_model_info == ("mlx-community/x", True)


def test_repo_files_with_sizes_empty_siblings() -> None:
    stub = _StubApi(siblings=[])
    assert repo_files_with_sizes(stub, "mlx-community/empty") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# free_disk_bytes
# ---------------------------------------------------------------------------


def test_free_disk_bytes_existing_and_missing_leaf(tmp_path: Path) -> None:
    # existing dir
    free = free_disk_bytes(tmp_path)
    assert isinstance(free, int) and free > 0

    # missing leaf walks up to existing ancestor
    missing = tmp_path / "no" / "pe"
    free2 = free_disk_bytes(missing)
    assert isinstance(free2, int) and free2 > 0


# ---------------------------------------------------------------------------
# fits_disk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "free", "expected"),
    [
        (100, 200, True),
        (200, 100, False),
        (100, None, None),
    ],
)
def test_fits_disk_parametrized(
    size: int, free: int | None, expected: bool | None
) -> None:
    assert fits_disk(size, free) is expected


# ---------------------------------------------------------------------------
# _throttled_tqdm (replaces DownloadProgress + make_progress_tqdm)
# ---------------------------------------------------------------------------


def _make_bar(
    on_progress: Callable[[int, int], None] | None,
    cancel_event: threading.Event | None,
    desc: str,
    bar_format: str,
    total: int = 0,
) -> Any:  # type: ignore[no-untyped-def]
    TqdmClass = _throttled_tqdm(on_progress, cancel_event)
    bar = TqdmClass(
        desc=desc,
        total=total,
        initial=0,
        unit="B",
        unit_scale=True,
        bar_format=bar_format,
        name="x",
        disable=True,
    )
    return bar, TqdmClass


def test_progress_forwards_throttled_snapshots() -> None:
    snapshots: list[tuple[int, int]] = []

    def on_progress(done: int, expected: int) -> None:
        snapshots.append((done, expected))

    cancel = threading.Event()
    bar, TqdmClass = _make_bar(
        on_progress,
        cancel,
        desc="Reconstructing (incomplete total...)",
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        total=0,
    )
    # hub's _AggregatedTqdm grows total after construction
    bar.total = 1000  # type: ignore[attr-defined]
    bar.update(400)
    assert snapshots == [(400, 1000)]

    # immediate second update should be throttled (no new snapshot)
    TqdmClass._mlx_state["last_flush"] = time.monotonic()  # type: ignore[attr-defined]
    bar.update(100)
    assert len(snapshots) == 1
    # totals still advanced despite throttling
    assert TqdmClass._mlx_state["disk_bytes"] == 500  # type: ignore[attr-defined]

    # after resetting throttle window, next update flushes again
    TqdmClass._mlx_state["last_flush"] = 0.0  # type: ignore[attr-defined]
    bar.update(100)
    assert len(snapshots) == 2
    # disk_bytes = 400 + 100 + 100 = 600; expected remains 1000
    assert snapshots[-1] == (600, 1000)
    bar.close()


def test_progress_ignores_xet_network_bar() -> None:
    snapshots: list[tuple[int, int]] = []

    def on_progress(done: int, expected: int) -> None:
        snapshots.append((done, expected))

    cancel = threading.Event()
    # reconstruction bar and xet bar share same on_progress/cancel/state via same class
    TqdmClass = _throttled_tqdm(on_progress, cancel)
    recon_bar = TqdmClass(
        desc="Reconstructing (incomplete total...)",
        total=1000,
        initial=0,
        unit="B",
        unit_scale=True,
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        name="x",
        disable=True,
    )
    xet_bar = TqdmClass(
        desc="Downloading bytes",
        total=982,
        initial=0,
        unit="B",
        unit_scale=True,
        bar_format="{desc}: {bar}| {n_fmt:>5}B{postfix:>12}",
        name="x",
        disable=True,
    )
    # xet update must not affect disk totals or emit snapshot
    xet_bar.update(982)
    assert TqdmClass._mlx_state["disk_bytes"] == 0  # type: ignore[attr-defined]
    assert snapshots == []

    recon_bar.update(500)
    assert TqdmClass._mlx_state["disk_bytes"] == 500  # type: ignore[attr-defined]
    assert len(snapshots) == 1
    recon_bar.close()
    xet_bar.close()


def test_progress_negative_delta_never_reduces_display() -> None:
    cancel = threading.Event()
    bar, TqdmClass = _make_bar(
        lambda _d, _e: None,
        cancel,
        desc="Reconstructing (incomplete total...)",
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        total=1000,
    )
    bar.update(300)
    assert TqdmClass._mlx_state["disk_bytes"] == 300  # type: ignore[attr-defined]
    bar.update(-50)
    assert TqdmClass._mlx_state["disk_bytes"] == 300  # type: ignore[attr-defined]
    bar.close()


def test_cancel_raises_and_is_shared_across_bars() -> None:
    cancel = threading.Event()
    bar, TqdmClass = _make_bar(
        lambda _d, _e: None,
        cancel,
        desc="Reconstructing (incomplete total...)",
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        total=100,
    )
    cancel.set()
    with pytest.raises(CancelledDownload):
        bar.update(1)

    # freshly constructed bar of same class shares the event
    bar2 = TqdmClass(
        desc="Reconstructing (incomplete total...)",
        total=0,
        initial=0,
        unit="B",
        unit_scale=True,
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        name="x",
        disable=True,
    )
    with pytest.raises(CancelledDownload):
        bar2.update(1)
    bar.close()
    bar2.close()


def test_close_flushes_final_totals() -> None:
    snapshots: list[tuple[int, int]] = []

    def on_progress(done: int, expected: int) -> None:
        snapshots.append((done, expected))

    cancel = threading.Event()
    bar, TqdmClass = _make_bar(
        on_progress,
        cancel,
        desc="Reconstructing (incomplete total...)",
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        total=1000,
    )
    bar.total = 1000  # type: ignore[attr-defined]
    # throttle window blocks immediate second flush; keep last_flush recent
    TqdmClass._mlx_state["last_flush"] = time.monotonic()  # type: ignore[attr-defined]
    bar.update(400)
    # suppressed due to throttle → only initial snapshot from earlier? Actually first
    # update after setting _last_flush recent should be suppressed → no snapshot yet
    # so clear any prior and force a fresh scenario:
    snapshots.clear()
    # Now updates remain throttled
    bar.update(100)
    assert len(snapshots) == 0
    # close must flush final totals exactly once
    bar.close()
    assert snapshots == [(500, 1000)]


def test_close_does_not_flush_when_cancelled() -> None:
    snapshots: list[tuple[int, int]] = []
    cancel = threading.Event()
    bar, TqdmClass = _make_bar(
        lambda d, e: snapshots.append((d, e)),  # type: ignore[no-untyped-def]
        cancel,
        desc="Reconstructing (incomplete total...)",
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        total=100,
    )
    bar.update(50)
    snapshots.clear()
    cancel.set()
    # close should not emit when cancelled
    bar.close()
    assert snapshots == []
    # xet bar close also should not emit
    xet_bar, _ = _make_bar(
        lambda d, e: snapshots.append((d, e)),  # type: ignore[no-untyped-def]
        cancel,
        desc="Downloading bytes",
        bar_format="{desc}: {bar}| {n_fmt:>5}B{postfix:>12}",
        total=100,
    )
    xet_bar.close()
    assert snapshots == []


def test_download_snapshot_pins_patterns_and_plumbs_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def recorder(repo_id: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["repo_id"] = repo_id
        captured.update(kwargs)
        return "/tmp/fake"

    monkeypatch.setattr(search_mod, "snapshot_download", recorder)

    ev = threading.Event()
    calls: list[tuple[int, int]] = []

    def on_progress(d: int, e: int) -> None:
        calls.append((d, e))

    search_mod.download_snapshot("org/m", on_progress=on_progress, cancel_event=ev)

    assert captured["repo_id"] == "org/m"
    assert captured["allow_patterns"] is ALLOW_PATTERNS
    assert captured["cache_dir"] is None
    tqdm_class = captured["tqdm_class"]
    assert isinstance(tqdm_class, type)

    # instance not cancelled yet should not raise
    inst = tqdm_class(
        desc="Reconstructing (incomplete total...)",
        total=0,
        initial=0,
        unit="B",
        unit_scale=True,
        bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B",
        name="x",
        disable=True,
    )
    inst.total = 100  # type: ignore[attr-defined]
    inst.update(10)  # should not raise

    ev.set()
    with pytest.raises(CancelledDownload):
        inst.update(1)
    inst.close()
