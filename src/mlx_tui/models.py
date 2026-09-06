"""Pure mapping from the HF cache to Models-table rows, plus deletion."""

from __future__ import annotations

import re
from dataclasses import dataclass

from huggingface_hub import (
    CachedRepoInfo,
    CacheNotFound,
    HFCacheInfo,
    scan_cache_dir,
)


@dataclass(frozen=True)
class ModelRow:
    """One MLX-ish cached repo as rendered in the Models tab."""

    repo_id: str
    size_on_disk: int  # bytes, summed across the repo's revisions by hub
    quant: str  # parsed label, "—" when unknown
    fits: bool | None  # None when system available-memory is unknown
    revision_hashes: tuple[str, ...]


_QUANT_RE = re.compile(
    r"[-_]([0-9]+(?:\.[0-9]+)?bit|bf16|fp16|f16|f32|int[48])$", re.IGNORECASE
)  # IGNORECASE so "…-8BIT" matches too; label is lowercased on return

_REQUIRED_FILES = frozenset({"config.json", "tokenizer_config.json"})

_HEADROOM_MARGIN_RATIO = 0.2


def quant_label(repo_id: str) -> str:
    """Extract a quant suffix from the repo's basename, e.g. "…-MLX-4bit" → "4bit"."""
    match = _QUANT_RE.search(repo_id.rsplit("/", 1)[-1])
    return match.group(1).lower() if match else "—"


def _is_mlx_model(repo: CachedRepoInfo) -> bool:
    """Loadability heuristic mirroring upstream: config + tokenizer + weights.

    File names are already in memory (no IO); dropping upstream's
    ``model.safetensors.index.json`` alternative keeps single-shard repos
    visible.
    """
    if repo.repo_type != "model":
        return False
    for revision in repo.revisions:
        names = {f.file_name for f in revision.files}
        if _REQUIRED_FILES <= names and any(
            f.file_name.endswith(".safetensors") for f in revision.files
        ):
            return True
    return False


def fits_headroom(size_on_disk: int, avail_gib: float | None) -> bool | None:
    """✓/⚠ hint with a 20%-of-size margin; indeterminate when memory is unknown."""
    if avail_gib is None:
        return None
    return size_on_disk * (1 + _HEADROOM_MARGIN_RATIO) <= avail_gib * 2**30


def collect_rows(info: HFCacheInfo, avail_gib: float | None) -> list[ModelRow]:
    """Map MLX-ish cache repos to rows, biggest first, repo_id breaking ties."""
    rows: list[ModelRow] = [
        ModelRow(
            repo_id=r.repo_id,
            size_on_disk=r.size_on_disk,
            quant=quant_label(r.repo_id),
            fits=fits_headroom(r.size_on_disk, avail_gib),
            revision_hashes=tuple(sorted(rev.commit_hash for rev in r.revisions)),
        )
        for r in info.repos
        if _is_mlx_model(r)
    ]
    rows.sort(key=_row_sort_key)
    return rows


def _row_sort_key(row: ModelRow) -> tuple[int, str]:
    return (-row.size_on_disk, row.repo_id)


def scan_models(avail_gib: float | None) -> list[ModelRow]:
    """The only cache touchpoint: scan the HF cache into table rows.

    A missing cache directory is not an error — an empty table.
    """
    try:
        info = scan_cache_dir()
    except CacheNotFound:
        return []
    return collect_rows(info, avail_gib)


def delete_repos(revision_hashes: tuple[str, ...]) -> int:
    """Delete every given revision from the cache; returns bytes freed.

    Raises ``CacheNotFound`` if the cache vanished mid-session — the caller
    maps that to a log line.
    """
    info = scan_cache_dir()
    strategy = info.delete_revisions(*revision_hashes)
    freed = strategy.expected_freed_size
    strategy.execute()
    return freed
