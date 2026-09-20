"""Build model facts from inert metadata; never import repository code."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mlx_tui.catalog.assessment import ModelFacts
from mlx_tui.search import RepoSnapshot, filtered_download_size


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _field(config: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        result = _positive_int(config.get(name))
        if result is not None:
            return result
    return None


def _attention_reason(
    architecture: str | None,
    config: Mapping[str, Any],
    text_config: Mapping[str, Any],
) -> str | None:
    """Explain why the conventional attention estimator cannot be used."""
    if not architecture:
        return "Model architecture is missing"
    if text_config:
        return "Nested text_config architecture is unsupported by the estimator"

    for source in (config, text_config):
        for key in (
            "sliding_window",
            "sliding_window_size",
            "use_sliding_window",
            "layer_types",
            "attention_pattern",
        ):
            if key not in source:
                continue
            value = source[key]
            # These are the only explicit disabled values accepted by the
            # frozen conventional configs. Other falsey or malformed values
            # remain unfamiliar rather than silently becoming conventional.
            if value is None or value is False:
                continue
            return f"Attention layout option {key} is active or unfamiliar"

    if architecture.lower() in {
        "mamba",
        "mamba2",
        "jamba",
        "recurrentgemma",
    }:
        return f"Architecture {architecture} has no validated attention estimator"
    return None


def facts_from_metadata(  # noqa: PLR0913
    repo_id: str,
    snapshot: RepoSnapshot,
    config: Mapping[str, Any] | None,
    *,
    weight_index: Mapping[str, Any] | None = None,
    additional_download_bytes: int | None = None,
    files_complete: bool = True,
) -> ModelFacts:
    names = {name for name, _ in snapshot.files}
    config = config or {}
    architecture = config.get("model_type")
    architecture = architecture if isinstance(architecture, str) else None
    foreign_quant = config.get("quantization_config")
    custom = (
        bool(config.get("auto_map"))
        or bool(config.get("trust_remote_code"))
        or isinstance(config.get("requires_runtime"), str)
        or (
            isinstance(foreign_quant, dict)
            and isinstance(foreign_quant.get("quant_method"), str)
            and foreign_quant["quant_method"] in {"gptq", "awq", "bitsandbytes"}
        )
    )
    if architecture and "bonsai" in architecture.lower():
        custom = True
    reason = (
        "Custom loader or foreign quantization runtime required" if custom else None
    )
    if not {"config.json", "tokenizer_config.json"} <= names or not any(
        name.endswith(".safetensors") for name in names
    ):
        reason = "Required configuration, tokenizer, or safetensors weights are absent"

    referenced: set[str] | None = None
    index_expected = "model.safetensors.index.json" in names
    if weight_index is not None:
        weight_map = weight_index.get("weight_map")
        if (
            isinstance(weight_map, dict)
            and weight_map
            and all(isinstance(value, str) for value in weight_map.values())
        ):
            referenced = set(weight_map.values())
    if index_expected and referenced is None:
        reason = "Weight index is unavailable or invalid"
        weights = set()
    elif referenced is None:
        weights = {name for name in names if name.endswith(".safetensors")}
    else:
        weights = referenced
        if not weights <= names:
            reason = "Weight index references missing shards"
    sizes = {name: size for name, size in snapshot.files}
    weight_bytes = (
        sum(size for name in weights if (size := sizes[name]) is not None)
        if weights
        and weights <= names
        and all(sizes[name] is not None for name in weights)
        else None
    )
    hidden = _field(config, "hidden_size", "n_embd")
    heads = _field(config, "num_attention_heads", "n_head")
    head_dim = _field(config, "head_dim")
    if head_dim is None and hidden and heads and hidden % heads == 0:
        head_dim = hidden // heads
    kv_heads = _field(config, "num_key_value_heads", "n_kv_heads") or heads
    layers = _field(config, "num_hidden_layers", "n_layer")
    nested_config = config.get("text_config")
    text_config: Mapping[str, Any] = (
        nested_config if isinstance(nested_config, dict) else {}
    )
    attention_reason = _attention_reason(architecture, config, text_config)
    if (
        "text_config" in config
        and nested_config is not None
        and not isinstance(nested_config, dict)
    ):
        attention_reason = "Nested text_config metadata is malformed"
    quant = config.get("quantization")
    quantization_contract_known = quant is None or (
        isinstance(quant, dict)
        and _positive_int(quant.get("bits")) is not None
        and _positive_int(quant.get("group_size")) is not None
    )
    quantization = (
        json.dumps(quant, sort_keys=True) if isinstance(quant, dict) else None
    )
    modules = config.get("modules")
    if isinstance(modules, list):
        dtypes = sorted(
            {
                item["dtype"]
                for item in modules
                if isinstance(item, dict) and isinstance(item.get("dtype"), str)
            }
        )
        if dtypes:
            quantization = (
                f"{quantization or 'unknown base'}; module dtypes: {', '.join(dtypes)}"
            )
    return ModelFacts(
        repo_id=repo_id,
        revision=snapshot.revision,
        architecture=architecture,
        weight_bytes=weight_bytes,
        download_bytes=filtered_download_size(snapshot.files)
        if snapshot.files and files_complete
        else None,
        additional_download_bytes=additional_download_bytes,
        layers=layers,
        kv_heads=kv_heads,
        head_dim=head_dim,
        quantization=quantization,
        quantization_contract_known=quantization_contract_known,
        custom_loader=custom,
        conventional_attention=attention_reason is None and not custom,
        reason=reason,
        attention_reason=attention_reason,
    )


def read_cached_config(snapshot_path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads((snapshot_path / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_cached_index(snapshot_path: Path) -> Mapping[str, Any] | None:
    path = snapshot_path / "model.safetensors.index.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
