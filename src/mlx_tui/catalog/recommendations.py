"""Small, reviewed task evidence with conservative revision scope."""

from __future__ import annotations

from dataclasses import dataclass

from mlx_tui.catalog.assessment import ModelAssessment, recommendation_group

CATALOG_VERSION = 1


@dataclass(frozen=True)
class TaskEvidence:
    repo_id: str
    revision: str
    task: str
    scope: str
    source: str
    reviewed: str
    explanation: str


# The upstream model card documents these purposes for the Qwen3 family.
# Application of that evidence to this converted MLX variant is indirect.
_QWEN3_REVISION = "3b1b1768f8f8cf8351c712464f906e86c2b8269e"
_SOURCE = "https://huggingface.co/Qwen/Qwen3-1.7B"
_EVIDENCE = (
    TaskEvidence(
        "mlx-community/Qwen3-1.7B-4bit",
        _QWEN3_REVISION,
        "chat",
        "family",
        _SOURCE,
        "2026-09-19",
        "Upstream family documents general dialogue; converted variant not benchmarked here.",
    ),
    TaskEvidence(
        "mlx-community/Qwen3-1.7B-4bit",
        _QWEN3_REVISION,
        "reasoning",
        "family",
        _SOURCE,
        "2026-09-19",
        "Upstream family documents a thinking mode; converted variant not benchmarked here.",
    ),
    TaskEvidence(
        "mlx-community/Qwen3-1.7B-4bit",
        _QWEN3_REVISION,
        "coding",
        "family",
        _SOURCE,
        "2026-09-19",
        "Upstream family documents coding use; this is purpose evidence, not a quality score.",
    ),
)


def task_evidence(repo_id: str, revision: str | None, task: str) -> TaskEvidence | None:
    return next(
        (
            item
            for item in _EVIDENCE
            if item.repo_id == repo_id
            and item.revision == revision
            and item.task == task
        ),
        None,
    )


def suggested_evidence(task: str) -> tuple[TaskEvidence, ...]:
    """Reviewed candidates to inspect before compatibility and fit are known."""
    return tuple(item for item in _EVIDENCE if item.task == task)


def rank_candidates(
    candidates: list[tuple[str, ModelAssessment, TaskEvidence | None, int]],
) -> list[tuple[str, ModelAssessment, TaskEvidence | None, int]]:
    """Group first, then evidence scope, headroom, popularity, stable ID."""
    group_order = {"Recommended": 0, "Consider with less headroom": 1, "All": 2}

    def key(
        item: tuple[str, ModelAssessment, TaskEvidence | None, int],
    ) -> tuple[int, int, int, int, str]:
        repo_id, assessment, evidence, downloads = item
        group = recommendation_group(assessment, evidence is not None)
        tier = 0 if evidence is not None and evidence.scope == "variant" else 1
        headroom = (
            assessment.budget_bytes - assessment.peak_bytes
            if assessment.budget_bytes is not None and assessment.peak_bytes is not None
            else -1
        )
        return group_order[group], tier, -headroom, -downloads, repo_id

    return sorted(candidates, key=key)
