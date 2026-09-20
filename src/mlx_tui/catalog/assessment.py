"""Pure compatibility, memory, and recommendation policy.

Estimates describe one retained sequence on the local Mac. They are not load
tests, and intentionally decline to estimate unfamiliar attention layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

GIB = 2**30
ESTIMATOR_VERSION = 1


class Compatibility(StrEnum):
    SUPPORTED = "Supported"
    DIFFERENT_RUNTIME = "Requires different runtime"
    UNKNOWN = "Unknown"


class Fit(StrEnum):
    COMFORTABLE = "Comfortable estimate"
    TIGHT = "Tight estimate"
    EXCEEDS = "Exceeds budget"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class HardwareProfile:
    chip: str | None
    total_bytes: int | None
    available_bytes: int | None
    recommended_working_set_bytes: int | None
    local: bool = True
    os_name: str | None = None
    architecture: str | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    identity: str | None
    architectures: frozenset[str]
    verified: bool
    unsupported_architectures: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ModelFacts:
    repo_id: str
    revision: str | None
    architecture: str | None
    weight_bytes: int | None
    download_bytes: int | None
    additional_download_bytes: int | None
    layers: int | None
    kv_heads: int | None
    head_dim: int | None
    quantization: str | None = None
    quantization_contract_known: bool = True
    custom_loader: bool = False
    conventional_attention: bool = False
    reason: str | None = None
    attention_reason: str | None = None


@dataclass(frozen=True)
class AssessmentScenario:
    context_tokens: int
    sequences: int = 1
    cache_bytes_per_element: int = 2


@dataclass(frozen=True)
class ModelAssessment:
    compatibility: Compatibility
    compatibility_reason: str
    fit: Fit
    peak_bytes: int | None
    weight_bytes: int | None
    kv_bytes: int | None
    allowance_bytes: int | None
    budget_bytes: int | None
    available_now: bool | None
    assumptions: tuple[str, ...]
    memory_fit_reason: str


def stable_memory_budget_bytes(hardware: HardwareProfile) -> int | None:
    """Return the stable estimate budget used by :func:`assess`."""
    total = hardware.total_bytes
    if total is None or total <= 0:
        return None
    reserve = max(4 * GIB, total // 5)
    budget = max(0, total - reserve)
    if hardware.recommended_working_set_bytes is not None:
        budget = min(budget, hardware.recommended_working_set_bytes)
    return budget


def _compatibility(  # noqa: PLR0911
    facts: ModelFacts, runtime: RuntimeCapabilities
) -> tuple[Compatibility, str]:
    if facts.custom_loader:
        return (
            Compatibility.DIFFERENT_RUNTIME,
            facts.reason or "Repository requires a custom loader",
        )
    if not runtime.verified:
        return Compatibility.UNKNOWN, "Executing runtime is unverified"
    if facts.reason:
        return Compatibility.UNKNOWN, facts.reason
    if not facts.quantization_contract_known:
        return Compatibility.UNKNOWN, "Quantization contract is not recognized"
    if not facts.revision or not facts.architecture:
        return Compatibility.UNKNOWN, "Model metadata is incomplete"
    if facts.architecture in runtime.unsupported_architectures:
        return (
            Compatibility.DIFFERENT_RUNTIME,
            f"{facts.architecture} is not supported by {runtime.identity}",
        )
    if facts.architecture not in runtime.architectures:
        return (
            Compatibility.UNKNOWN,
            f"{facts.architecture} has not been verified for {runtime.identity}",
        )
    return (
        Compatibility.SUPPORTED,
        f"{facts.architecture} inspected for {runtime.identity}",
    )


def assess(  # noqa: PLR0912
    facts: ModelFacts,
    hardware: HardwareProfile,
    runtime: RuntimeCapabilities,
    scenario: AssessmentScenario,
) -> ModelAssessment:
    compatibility, reason = _compatibility(facts, runtime)

    budget = stable_memory_budget_bytes(hardware)

    assumptions = (
        f"estimator v{ESTIMATOR_VERSION}; {scenario.sequences} active sequence(s)",
        f"{scenario.context_tokens} retained tokens including generated tokens",
        "runtime allowance is at least 1 GiB or 10% of weights; prefill may exceed it",
        "local estimate; swaps and concurrent clients may require more memory",
    )
    weight = facts.weight_bytes
    if weight is None:
        memory_reason = "Resident weights are missing or their size is unknown"
    elif compatibility is Compatibility.DIFFERENT_RUNTIME:
        memory_reason = "Memory estimate is unavailable for the incompatible runtime"
    elif not facts.conventional_attention:
        memory_reason = facts.attention_reason or (
            "Attention layout is unsupported by the memory estimator"
        )
    elif any(
        v is None or v <= 0 for v in (facts.layers, facts.kv_heads, facts.head_dim)
    ):
        memory_reason = "Model dimensions are missing or invalid"
    elif (
        scenario.context_tokens <= 0
        or scenario.sequences <= 0
        or scenario.cache_bytes_per_element <= 0
    ):
        memory_reason = "Assessment scenario is invalid"
    elif budget is None:
        memory_reason = "Stable memory budget is unavailable"
    else:
        memory_reason = "Complete estimate is available"

    kv: int | None = None
    if (
        facts.conventional_attention
        and compatibility is not Compatibility.DIFFERENT_RUNTIME
        and all(
            v is not None and v > 0
            for v in (facts.layers, facts.kv_heads, facts.head_dim)
        )
        and scenario.context_tokens > 0
        and scenario.sequences > 0
        and scenario.cache_bytes_per_element > 0
    ):
        assert facts.layers is not None
        assert facts.kv_heads is not None
        assert facts.head_dim is not None
        kv = (
            2
            * facts.layers
            * scenario.context_tokens
            * facts.kv_heads
            * facts.head_dim
            * scenario.cache_bytes_per_element
            * scenario.sequences
        )
    allowance = max(GIB, weight // 10) if weight is not None else None
    peak = (
        weight + kv + allowance
        if weight is not None and kv is not None and allowance is not None
        else None
    )
    if peak is not None and budget is not None:
        if peak > budget:
            fit = Fit.EXCEEDS
            memory_reason = "Complete estimate exceeds the stable memory budget"
        elif peak > budget * 0.8:
            fit = Fit.TIGHT
            memory_reason = (
                "Complete estimate leaves limited headroom in the stable budget"
            )
        else:
            fit = Fit.COMFORTABLE
            memory_reason = "Complete estimate is within the stable memory budget"
    elif weight is not None and budget is not None and weight > budget:
        fit = Fit.EXCEEDS
        memory_reason = (
            "Resident weights alone exceed the stable memory budget (lower bound)"
        )
    else:
        fit = Fit.UNKNOWN
    available = (
        peak <= hardware.available_bytes
        if peak is not None and hardware.available_bytes is not None
        else None
    )
    return ModelAssessment(
        compatibility=compatibility,
        compatibility_reason=reason,
        fit=fit,
        peak_bytes=peak,
        weight_bytes=weight,
        kv_bytes=kv,
        allowance_bytes=allowance,
        budget_bytes=budget,
        available_now=available,
        assumptions=assumptions,
        memory_fit_reason=memory_reason,
    )


def recommendation_group(assessment: ModelAssessment, has_task_evidence: bool) -> str:
    if assessment.compatibility is not Compatibility.SUPPORTED or not has_task_evidence:
        return "All"
    if assessment.fit is Fit.COMFORTABLE:
        return "Recommended"
    if assessment.fit is Fit.TIGHT:
        return "Consider with less headroom"
    return "All"
