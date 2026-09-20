"""Frozen metadata and arithmetic coverage for advisory assessments."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from mlx_tui.catalog.assessment import (
    GIB,
    AssessmentScenario,
    Compatibility,
    Fit,
    HardwareProfile,
    RuntimeCapabilities,
    assess,
)
from mlx_tui.catalog.facts import facts_from_metadata
from mlx_tui.catalog.recommendations import (
    rank_candidates,
    suggested_evidence,
    task_evidence,
)
from mlx_tui.search import RepoSnapshot, filtered_download_size

RUNTIME = RuntimeCapabilities("managed MLX-LM", frozenset({"qwen2"}), True)
MAC = HardwareProfile("Test Mac", 32 * GIB, 20 * GIB, 26 * GIB)
CONVENTIONAL_FIXTURES = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "conventional_attention_configs.json"
    ).read_text()
)


def _qwen():  # type: ignore[no-untyped-def]
    return facts_from_metadata(
        "another-publisher/Qwen-4bit",
        RepoSnapshot(
            "a" * 40,
            (
                ("config.json", 100),
                ("tokenizer_config.json", 100),
                ("model.safetensors", 4 * GIB),
            ),
        ),
        {
            "model_type": "qwen2",
            "num_hidden_layers": 32,
            "num_attention_heads": 32,
            "num_key_value_heads": 8,
            "hidden_size": 4096,
        },
    )


def test_gqa_context_scales_kv_and_not_weights() -> None:
    facts = _qwen()
    short = assess(facts, MAC, RUNTIME, AssessmentScenario(8192))
    long = assess(facts, MAC, RUNTIME, AssessmentScenario(32768))
    assert short.compatibility is Compatibility.SUPPORTED
    assert short.kv_bytes == 2 * 32 * 8192 * 8 * 128 * 2
    assert short.kv_bytes is not None
    assert long.kv_bytes == 4 * short.kv_bytes
    assert long.weight_bytes == short.weight_bytes == 4 * GIB
    assert short.fit is Fit.COMFORTABLE


def test_bonsai_requires_custom_loader_and_is_not_comfortable() -> None:
    fixture = json.loads(
        (Path(__file__).parent.parent / "fixtures" / "bonsai_metadata.json").read_text()
    )
    facts = facts_from_metadata(
        fixture["repo_id"],
        RepoSnapshot(
            fixture["revision"], tuple(tuple(item) for item in fixture["files"])
        ),
        fixture["config"],
    )
    result = assess(facts, MAC, RUNTIME, AssessmentScenario(8192))
    assert result.compatibility is Compatibility.DIFFERENT_RUNTIME
    assert result.fit is Fit.UNKNOWN
    assert facts.revision == fixture["revision"]
    assert facts.architecture == "prism_hadamard_qwen35"
    assert facts.weight_bytes == 8595477990
    assert "float16" in (facts.quantization or "")


def test_missing_size_stays_unknown_and_never_comfortable() -> None:
    snapshot = RepoSnapshot(
        "c" * 40, (("config.json", 100), ("model.safetensors", None))
    )
    facts = facts_from_metadata("owner/model", snapshot, {"model_type": "qwen2"})
    assert facts.weight_bytes is None
    assert facts.download_bytes is None
    assert filtered_download_size(snapshot.files) is None
    assert assess(facts, MAC, RUNTIME, AssessmentScenario(8192)).fit is Fit.UNKNOWN


def test_local_snapshot_does_not_claim_complete_hub_download() -> None:
    facts = facts_from_metadata(
        "owner/model",
        RepoSnapshot(
            "c" * 40,
            (
                ("config.json", 10),
                ("tokenizer_config.json", 10),
                ("model.safetensors", 100),
            ),
        ),
        {"model_type": "qwen2"},
        files_complete=False,
    )
    assert facts.weight_bytes == 100
    assert facts.download_bytes is None
    assert facts.additional_download_bytes is None


def test_repeated_index_shards_count_once_and_missing_shard_unknown() -> None:
    snapshot = RepoSnapshot(
        "d" * 40,
        (("config.json", 100), ("a.safetensors", 123), ("b.safetensors", 456)),
    )
    index = {"weight_map": {"a": "a.safetensors", "b": "a.safetensors"}}
    assert (
        facts_from_metadata(
            "owner/model", snapshot, {}, weight_index=index
        ).weight_bytes
        == 123
    )
    missing = {"weight_map": {"a": "missing.safetensors"}}
    assert (
        facts_from_metadata(
            "owner/model", snapshot, {}, weight_index=missing
        ).weight_bytes
        is None
    )


def test_hybrid_and_unverified_runtime_remain_unknown() -> None:
    hybrid = replace(_qwen(), conventional_attention=False)
    assert assess(hybrid, MAC, RUNTIME, AssessmentScenario(8192)).fit is Fit.UNKNOWN
    attached = RuntimeCapabilities(None, frozenset(), False)
    assert (
        assess(_qwen(), MAC, attached, AssessmentScenario(8192)).compatibility
        is Compatibility.UNKNOWN
    )


def test_unrecognized_quantization_cannot_claim_support() -> None:
    facts = replace(_qwen(), quantization_contract_known=False)
    result = assess(facts, MAC, RUNTIME, AssessmentScenario(8192))
    assert result.compatibility is Compatibility.UNKNOWN


def test_unlisted_architecture_is_unknown_until_inspected() -> None:
    facts = replace(_qwen(), architecture="new_attention_model")
    assert (
        assess(facts, MAC, RUNTIME, AssessmentScenario(8192)).compatibility
        is Compatibility.UNKNOWN
    )


def test_recommendation_requires_revision_task_and_comfortable_fit() -> None:
    evidence = task_evidence(
        "mlx-community/Qwen3-1.7B-4bit",
        "3b1b1768f8f8cf8351c712464f906e86c2b8269e",
        "chat",
    )
    assert evidence is not None and evidence.scope == "family"
    assert suggested_evidence("chat") == (evidence,)
    assert task_evidence(evidence.repo_id, "e" * 40, "chat") is None
    comfortable = assess(_qwen(), MAC, RUNTIME, AssessmentScenario(8192))
    tight = replace(comfortable, fit=Fit.TIGHT)
    unknown = replace(comfortable, compatibility=Compatibility.UNKNOWN)
    ranked = rank_candidates(
        [
            ("unknown", unknown, evidence, 1000),
            ("tight", tight, evidence, 100),
            ("comfortable", comfortable, evidence, 1),
        ]
    )
    assert [item[0] for item in ranked] == ["comfortable", "tight", "unknown"]


def _conventional_snapshot() -> RepoSnapshot:
    return RepoSnapshot(
        "f" * 40,
        (
            ("config.json", 100),
            ("tokenizer_config.json", 100),
            ("model.safetensors", 4 * GIB),
        ),
    )


def _conventional_config() -> dict[str, object]:
    return {
        "model_type": "qwen2",
        "num_hidden_layers": 32,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
        "hidden_size": 4096,
    }


def test_explicitly_disabled_attention_options_remain_conventional() -> None:
    for name in ("null_sliding_window", "disabled_sliding_window"):
        config = CONVENTIONAL_FIXTURES[name]
        facts = facts_from_metadata("owner/model", _conventional_snapshot(), config)
        assert facts.conventional_attention is True
        assert facts.attention_reason is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sliding_window", 4096),
        ("sliding_window", 0),
        ("attention_pattern", "unknown"),
        ("layer_types", []),
    ],
)
def test_active_or_malformed_attention_options_stay_unknown(
    key: str, value: object
) -> None:
    fixture_name = (
        "active_sliding_window"
        if key == "sliding_window" and value == 4096
        else "malformed_sliding_window"
        if key == "sliding_window" and value == 0
        else None
    )
    config = (
        CONVENTIONAL_FIXTURES[fixture_name]
        if fixture_name is not None
        else _conventional_config()
    )
    config[key] = value
    facts = facts_from_metadata("owner/model", _conventional_snapshot(), config)
    assert facts.conventional_attention is False
    assert facts.attention_reason is not None
    assert "attention" in facts.attention_reason.lower()


def test_nested_text_config_does_not_create_a_partial_estimate() -> None:
    config = _conventional_config()
    config["text_config"] = {"hidden_size": 4096, "num_hidden_layers": 32}
    facts = facts_from_metadata("owner/model", _conventional_snapshot(), config)
    result = assess(facts, MAC, RUNTIME, AssessmentScenario(8192))
    assert facts.conventional_attention is False
    assert facts.attention_reason is not None
    assert result.fit is Fit.UNKNOWN
    assert "nested" in result.memory_fit_reason.lower()


@pytest.mark.parametrize(
    ("facts", "hardware", "scenario", "reason"),
    [
        (replace(_qwen(), weight_bytes=None), MAC, AssessmentScenario(8192), "weight"),
        (
            replace(_qwen(), layers=None),
            MAC,
            AssessmentScenario(8192),
            "dimension",
        ),
        (
            replace(_qwen(), conventional_attention=False),
            MAC,
            AssessmentScenario(8192),
            "attention",
        ),
        (_qwen(), MAC, AssessmentScenario(0), "scenario"),
        (
            _qwen(),
            HardwareProfile("Test Mac", None, 20 * GIB, None),
            AssessmentScenario(8192),
            "budget",
        ),
    ],
)
def test_memory_fit_reason_explains_incomplete_estimates(
    facts: object,
    hardware: HardwareProfile,
    scenario: AssessmentScenario,
    reason: str,
) -> None:
    result = assess(facts, hardware, RUNTIME, scenario)  # type: ignore[arg-type]
    assert result.fit is Fit.UNKNOWN
    assert reason in result.memory_fit_reason.lower()


def test_weight_lower_bound_over_budget_has_distinct_reason() -> None:
    facts = replace(_qwen(), weight_bytes=40 * GIB, layers=None)
    result = assess(facts, MAC, RUNTIME, AssessmentScenario(8192))
    assert result.fit is Fit.EXCEEDS
    assert "lower bound" in result.memory_fit_reason.lower()


def test_unverified_runtime_has_independent_memory_explanation() -> None:
    result = assess(
        _qwen(),
        MAC,
        RuntimeCapabilities(None, frozenset(), False),
        AssessmentScenario(8192),
    )
    assert result.compatibility is Compatibility.UNKNOWN
    assert "runtime" in result.compatibility_reason.lower()
    assert result.fit is Fit.COMFORTABLE
    assert "complete" in result.memory_fit_reason.lower()
