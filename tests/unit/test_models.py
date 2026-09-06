"""Unit tests for mlx_tui.models using directly-constructed hub dataclasses."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from huggingface_hub import (
    CachedFileInfo,
    CachedRepoInfo,
    CachedRevisionInfo,
    HFCacheInfo,
)

import mlx_tui.models as models_mod
from mlx_tui.models import (
    ModelRow,
    collect_rows,
    delete_repos,
    fits_headroom,
    quant_label,
)

_MLX_FILES = ["config.json", "tokenizer_config.json", "model.safetensors"]

RepoTypeStr = Literal["model", "dataset", "space"]


def _file(name: str) -> CachedFileInfo:
    return CachedFileInfo(
        file_name=name,
        file_path=Path("x"),
        blob_path=Path("x"),
        size_on_disk=0,
        blob_last_accessed=0.0,
        blob_last_modified=0.0,
    )


def _revision(commit_hash: str, file_names: list[str]) -> CachedRevisionInfo:
    return CachedRevisionInfo(
        commit_hash=commit_hash,
        snapshot_path=Path("x"),
        size_on_disk=0,
        files=frozenset(_file(name) for name in file_names),
        refs=frozenset(["main"]),
        last_modified=0.0,
    )


def _repo(
    repo_id: str,
    size: int,
    file_names: list[str],
    *,
    repo_type: RepoTypeStr = "model",
) -> CachedRepoInfo:
    revision = _revision("rev0", file_names)
    return CachedRepoInfo(
        repo_id=repo_id,
        repo_type=repo_type,
        repo_path=Path("x"),
        size_on_disk=size,
        nb_files=len(file_names),
        revisions=frozenset({revision}),
        last_accessed=0.0,
        last_modified=0.0,
    )


def _info(*repos: CachedRepoInfo) -> HFCacheInfo:
    return HFCacheInfo(
        size_on_disk=sum(r.size_on_disk for r in repos),
        repos=frozenset(repos),
        incomplete_files=frozenset(),
        warnings=[],
    )


@pytest.mark.parametrize(
    ("repo_id", "expected"),
    [
        ("ornith-ai/Ornith-1.5-9B-MLX-4bit", "4bit"),
        ("Qwen3-1.7B-8bit", "8bit"),
        ("foo/model-bf16", "bf16"),
        ("foo/model-int4", "int4"),
        ("foo/Llama-3B", "—"),
        ("foo/model-8BIT", "8bit"),
    ],
)
def test_quant_label(repo_id: str, expected: str) -> None:
    assert quant_label(repo_id) == expected


def test_is_mlx_model_accepts_full_model() -> None:
    assert models_mod._is_mlx_model(_repo("foo/bar", 1, _MLX_FILES))


def test_is_mlx_model_rejects_missing_tokenizer() -> None:
    files = ["config.json", "model.safetensors"]
    assert not models_mod._is_mlx_model(_repo("foo/bar", 1, files))


def test_is_mlx_model_rejects_dataset_repo_type() -> None:
    repo = _repo("foo/bar", 1, _MLX_FILES, repo_type="dataset")
    assert not models_mod._is_mlx_model(repo)


def test_is_mlx_model_rejects_json_only_no_weights() -> None:
    files = ["config.json", "tokenizer_config.json", "README.md"]
    assert not models_mod._is_mlx_model(_repo("foo/bar", 1, files))


@pytest.mark.parametrize(
    ("size_on_disk", "avail_gib", "expected"),
    [
        (5_000_000_000, 10.0, True),
        (9_000_000_000, 10.0, False),
        (100, None, None),
    ],
)
def test_fits_headroom(
    size_on_disk: int, avail_gib: float | None, expected: bool | None
) -> None:
    assert fits_headroom(size_on_disk, avail_gib) is expected


def test_collect_rows_filters_and_sorts_big_first() -> None:
    big = _repo("mlx-community/big-4bit", 9_000_000_000, _MLX_FILES)
    small = _repo("mlx-community/small-8bit", 1_000_000_000, _MLX_FILES)
    dataset = _repo("user/some-dataset", 5_000_000_000, _MLX_FILES, repo_type="dataset")
    plain = _repo("org/plain-model", 4_000_000_000, ["README.md"])
    info = _info(big, small, dataset, plain)
    rows = collect_rows(info, avail_gib=None)
    assert [row.repo_id for row in rows] == [
        "mlx-community/big-4bit",
        "mlx-community/small-8bit",
    ]
    assert rows[0].quant == "4bit"
    assert rows[0].fits is None


def test_collect_rows_revision_hashes_sorted() -> None:
    repo = CachedRepoInfo(
        repo_id="mlx-community/multi-4bit",
        repo_type="model",
        repo_path=Path("x"),
        size_on_disk=2_000_000_000,
        nb_files=3,
        revisions=frozenset(
            {
                _revision("zzz", _MLX_FILES),
                _revision("aaa", _MLX_FILES),
                _revision("mmm", _MLX_FILES),
            }
        ),
        last_accessed=0.0,
        last_modified=0.0,
    )
    rows = collect_rows(_info(repo), avail_gib=None)
    assert rows[0].revision_hashes == ("aaa", "mmm", "zzz")


def test_collect_rows_empty_cache() -> None:
    assert collect_rows(_info(), avail_gib=None) == []


def test_scan_models_maps_missing_cache_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing() -> object:
        raise models_mod.CacheNotFound("gone", Path("/gone"))

    monkeypatch.setattr(models_mod, "scan_cache_dir", raise_missing)
    assert models_mod.scan_models(None) == []


def test_delete_repos_passes_hashes_and_returns_freed_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[bool] = []
    seen_hashes: tuple[str, ...] = ()

    class StubStrategy:
        expected_freed_size = 123

        @staticmethod
        def execute() -> None:
            executed.append(True)

    class StubInfo:
        def delete_revisions(self, *revisions: str) -> StubStrategy:
            nonlocal seen_hashes
            seen_hashes = revisions
            return StubStrategy()

    monkeypatch.setattr(models_mod, "scan_cache_dir", StubInfo)
    freed = delete_repos(("abc123", "def456"))
    assert freed == 123
    assert seen_hashes == ("abc123", "def456")
    assert executed == [True]


def test_row_dataclass_shape() -> None:
    row = ModelRow("m", 1, "4bit", True, ("h",))
    assert (row.repo_id, row.size_on_disk, row.quant, row.fits) == (
        "m",
        1,
        "4bit",
        True,
    )
