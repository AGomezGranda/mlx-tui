"""Pure mapping from the HF cache to Models-table rows, plus deletion."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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
    quant: str  # name-derived hint, "—" when unknown
    revision_hashes: tuple[str, ...]


_QUANT_RE = re.compile(
    r"[-_]([0-9]+(?:\.[0-9]+)?bit|bf16|fp16|f16|f32|int[48])$", re.IGNORECASE
)  # IGNORECASE so "…-8BIT" matches too; label is lowercased on return

_REQUIRED_FILES = frozenset({"config.json", "tokenizer_config.json"})


def verify_cached_assets(snapshot: Path, required_files: Iterable[str] = ()) -> None:
    """Verify the local files needed before asking MLX-LM to load a snapshot."""
    root = Path(snapshot)
    if not root.is_dir():
        raise ValueError(f"snapshot is not a directory: {root}")

    def asset(name: str) -> Path:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"invalid snapshot asset name: {name}")
        path = root / candidate
        if not path.exists():
            raise ValueError(f"snapshot asset is missing: {path}")
        resolved = path.resolve()
        allowed = {root.resolve()}
        if root.parent.name == "snapshots":
            allowed.add((root.parent.parent / "blobs").resolve())
        if not any(resolved.is_relative_to(base) for base in allowed):
            raise ValueError(f"snapshot asset escapes its cache: {path}")
        return path

    for name in (*_REQUIRED_FILES, *required_files):
        asset(name)
    index = root / "model.safetensors.index.json"
    if index.exists():
        try:
            value = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid weight index: {index}") from exc
        weight_map = value.get("weight_map") if isinstance(value, dict) else None
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError(f"weight index has no shards: {index}")
        for name in weight_map.values():
            if not isinstance(name, str):
                raise ValueError(f"invalid weight shard in {index}")
            asset(name)
    elif not any(path.is_file() for path in root.glob("*.safetensors")):
        raise ValueError(f"snapshot has no safetensors weights: {root}")


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


def collect_rows(info: HFCacheInfo) -> list[ModelRow]:
    """Map MLX-ish cache repos to rows, biggest first, repo_id breaking ties."""
    rows: list[ModelRow] = [
        ModelRow(
            repo_id=r.repo_id,
            size_on_disk=r.size_on_disk,
            quant=quant_label(r.repo_id),
            revision_hashes=tuple(sorted(rev.commit_hash for rev in r.revisions)),
        )
        for r in info.repos
        if _is_mlx_model(r)
    ]
    rows.sort(key=lambda r: (-r.size_on_disk, r.repo_id))  # type: ignore[implicit-any-lambda]
    return rows


def scan_models() -> list[ModelRow]:
    """The only cache touchpoint: scan the HF cache into table rows.

    A missing cache directory is not an error — an empty table.
    """
    try:
        info = scan_cache_dir()
    except CacheNotFound:
        return []
    return collect_rows(info)


def resolve_cached_snapshot(
    repo_id: str,
    revision: str,
    *,
    info: HFCacheInfo | None = None,
) -> Path:
    """Resolve an exact cached revision without contacting the Hub."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full immutable commit hash")
    cache = info if info is not None else scan_cache_dir()
    for repo in cache.repos:
        if repo.repo_type != "model" or repo.repo_id != repo_id:
            continue
        for cached_revision in repo.revisions:
            if cached_revision.commit_hash != revision:
                continue
            snapshot = Path(cached_revision.snapshot_path)
            if not snapshot.is_dir():
                raise FileNotFoundError(f"cached snapshot is missing: {snapshot}")
            return snapshot
    raise FileNotFoundError(f"cached snapshot not found: {repo_id}@{revision}")


def model_identity_matches(repo_id: str, identity: str | Path | None) -> bool:
    """Match a repository ID against either an ID or its HF snapshot path."""
    if identity is None:
        return False
    value = str(identity)
    if value == repo_id:
        return True
    cache_repo = f"models--{repo_id.replace('/', '--')}"
    parts = Path(value).parts
    try:
        cache_index = parts.index(cache_repo)
    except ValueError:
        return False
    return cache_index + 2 < len(parts) and parts[cache_index + 1] == "snapshots"


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
