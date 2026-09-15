"""Real-server Milestone A compatibility contracts.

These tests are intentionally opt-in and never start, stop, download, or
reconfigure the endpoint under test.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from mlx_tui.chat import stream_turn

_TIMEOUT = httpx.Timeout(60.0, connect=2.0)


def _payload(model: str | None, prompt: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32,
        "temperature": 0,
        "seed": 7,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if model is not None:
        payload["model"] = model
    payload.update(extra)
    return payload


def _request(runtime: Any, payload: dict[str, Any]) -> tuple[float, httpx.Response]:
    started = time.monotonic()
    response = httpx.post(
        f"{runtime.base_url}/v1/chat/completions",
        json=payload,
        timeout=_TIMEOUT,
    )
    return time.monotonic() - started, response


def _timed_stream(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    started = time.monotonic()
    done_seen = False
    with httpx.stream(
        "POST",
        f"{runtime.base_url}/v1/chat/completions",
        json={**payload, "stream": True, "stream_options": {"include_usage": True}},
        timeout=_TIMEOUT,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            at_s = time.monotonic() - started
            if not line:
                continue
            if line.startswith(":"):
                events.append({"at_s": at_s, "kind": "comment", "data": line})
                continue
            if not line.startswith("data:"):
                events.append({"at_s": at_s, "kind": "unknown", "data": line})
                continue
            raw = line.removeprefix("data:").strip()
            if raw == "[DONE]":
                done_seen = True
                events.append({"at_s": at_s, "kind": "done"})
                continue
            events.append({"at_s": at_s, "kind": "json", "data": json.loads(raw)})
    return {
        "status_code": response.status_code,
        "elapsed_s": time.monotonic() - started,
        "done_seen": done_seen,
        "events": events,
    }


def _chunks(stream: dict[str, Any]) -> list[dict[str, Any]]:
    return [event["data"] for event in stream["events"] if event["kind"] == "json"]


def test_health_catalogue_and_generation(contract_runtime: Any) -> None:
    runtime = contract_runtime
    with httpx.Client(base_url=runtime.base_url, timeout=_TIMEOUT) as client:
        health_before = client.get("/health")
        catalogue_before = client.get("/v1/models")
    health_before.raise_for_status()
    catalogue_before.raise_for_status()
    ids_before = [item["id"] for item in catalogue_before.json()["data"]]
    assert runtime.model in ids_before

    expected = "def answer(): return 42"
    elapsed, generated = _request(
        runtime,
        _payload(
            runtime.model,
            "Synthetic coding check v1. Return exactly this one-line Python "
            f"function and nothing else: {expected}",
        ),
    )
    generated.raise_for_status()
    body = generated.json()
    assert body["model"] == runtime.model
    assert body["choices"][0]["message"]["content"].strip() == expected
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["completion_tokens"] > 0

    with httpx.Client(base_url=runtime.base_url, timeout=_TIMEOUT) as client:
        health_after = client.get("/health")
        catalogue_after = client.get("/v1/models")
    assert health_after.status_code == 200
    assert catalogue_after.status_code == 200
    runtime.evidence.record(
        "health_catalogue_generation",
        status="supported",
        elapsed_s=elapsed,
        health_before=health_before.json(),
        catalogue_before=catalogue_before.json(),
        generation=body,
        health_after=health_after.json(),
        catalogue_after=catalogue_after.json(),
    )


def test_stream_reasoning_usage_and_tools(contract_runtime: Any) -> None:
    runtime = contract_runtime
    reasoning_stream = _timed_stream(
        runtime,
        _payload(
            runtime.model,
            "Think briefly, then answer with the single word: blue",
            max_tokens=96,
            chat_template_kwargs={"enable_thinking": True},
        ),
    )
    chunks = _chunks(reasoning_stream)
    assert reasoning_stream["done_seen"] is True
    assert all(chunk.get("model") == runtime.model for chunk in chunks)
    deltas = [
        choice.get("delta", {}) for chunk in chunks for choice in chunk["choices"]
    ]
    assert any(delta.get("reasoning") for delta in deltas)
    usage = next((chunk.get("usage") for chunk in chunks if chunk.get("usage")), None)
    assert usage is not None
    assert usage["completion_tokens"] > 0
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens")
    assert type(cached) is int and cached >= 0

    tool_payload = _payload(
        runtime.model,
        "What is the weather in Madrid? Use the weather tool.",
        max_tokens=96,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        tool_choice="required",
    )
    _, tool_response = _request(runtime, tool_payload)
    tool_response.raise_for_status()
    tool_body = tool_response.json()
    message = tool_body["choices"][0]["message"]
    structured = bool(message.get("tool_calls"))
    runtime.evidence.record(
        "stream_reasoning_usage",
        status="supported",
        stream=reasoning_stream,
    )
    runtime.evidence.record(
        "structured_tool_output",
        status="supported" if structured else "unavailable",
        response=tool_body,
        note=None if structured else "model returned text instead of tool_calls",
    )


def test_cache_prefix_sampler_and_missing_model(contract_runtime: Any) -> None:
    runtime = contract_runtime
    prefix = "Stable shared prefix for the cache contract. " * 12
    usages: list[dict[str, Any]] = []
    for suffix in ("alpha", "alpha", "beta"):
        _, response = _request(runtime, _payload(runtime.model, prefix + suffix))
        response.raise_for_status()
        usages.append(response.json()["usage"])

    accepted = _payload(
        runtime.model,
        "Reply OK",
        max_tokens=8,
        temperature=0.2,
        top_p=0.9,
        top_k=4,
        min_p=0.05,
    )
    _, accepted_response = _request(runtime, accepted)
    accepted_response.raise_for_status()

    invalid_outcome: dict[str, Any]
    try:
        _, invalid_response = _request(
            runtime, _payload(runtime.model, "x", max_tokens=1, top_p=2.0)
        )
        invalid_outcome = {
            "status_code": invalid_response.status_code,
            "body": invalid_response.text,
        }
        assert invalid_response.status_code >= 400
    except httpx.RemoteProtocolError as exc:
        invalid_outcome = {"protocol_disconnect": str(exc)}

    _, missing_response = _request(runtime, _payload(None, "Reply OK", max_tokens=8))
    missing_response.raise_for_status()
    missing_body = missing_response.json()
    assert missing_body["model"] == "default_model"
    runtime.evidence.record(
        "cache_prefix_sampler_missing_model",
        status="supported",
        prefix_usages=usages,
        accepted_sampler=accepted_response.json(),
        invalid_sampler=invalid_outcome,
        missing_model=missing_body,
        missing_model_rule=(
            "accepted and routed to server default; identity is reported only as "
            "default_model, so the TUI still requires explicit selection"
        ),
    )


def test_two_request_bound_and_disconnects(contract_runtime: Any) -> None:
    runtime = contract_runtime
    payloads = [
        _payload(runtime.model, f"Return exactly: concurrent-{index}", max_tokens=24)
        for index in range(2)
    ]
    aggregate_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_request, runtime, payload) for payload in payloads]
        results = [future.result() for future in futures]
    aggregate_s = time.monotonic() - aggregate_started
    assert all(response.status_code == 200 for _, response in results)

    url = f"{runtime.base_url}/v1/chat/completions"
    disconnects: list[dict[str, Any]] = []
    for consume_output in (False, True):
        started = time.monotonic()
        with httpx.stream(
            "POST",
            url,
            json={
                **_payload(
                    runtime.model, "Count slowly from one to fifty", max_tokens=96
                ),
                "stream": True,
            },
            timeout=_TIMEOUT,
        ) as response:
            response.raise_for_status()
            first_line = next(response.iter_lines(), None) if consume_output else None
        disconnects.append(
            {
                "after_output": consume_output,
                "first_line": first_line,
                "client_close_s": time.monotonic() - started,
            }
        )

    follow_up_s, follow_up = _request(runtime, _payload(runtime.model, "Reply OK"))
    follow_up.raise_for_status()
    runtime.evidence.record(
        "bounded_concurrency_disconnect",
        status="supported",
        request_latencies_s=[elapsed for elapsed, _ in results],
        aggregate_s=aggregate_s,
        disconnects=disconnects,
        follow_up_latency_s=follow_up_s,
        cancellation="unknown; client close and recovery do not prove engine cancellation",
    )


def test_failed_model_load_and_recovery(contract_runtime: Any) -> None:
    runtime = contract_runtime
    bad_model = "/definitely/missing-mlx-tui-contract-model"
    _, failed = _request(runtime, _payload(bad_model, "x", max_tokens=1))
    assert failed.status_code >= 400
    recovery_s, recovered = _request(runtime, _payload(runtime.model, "Reply OK"))
    recovered.raise_for_status()
    assert recovered.json()["model"] == runtime.model
    runtime.evidence.record(
        "failed_model_load_recovery",
        status="supported",
        failed_status=failed.status_code,
        failed_body=failed.text,
        recovery_latency_s=recovery_s,
        recovery=recovered.json(),
    )


async def test_corrected_client_stream_turn(contract_runtime: Any) -> None:
    """Rerun pinned contracts through corrected ``stream_turn`` + raw HTTP."""
    runtime = contract_runtime
    url = f"{runtime.base_url}/v1/chat/completions"
    expected = "def answer(): return 42"
    coding_payload: dict[str, Any] = {
        **_payload(
            runtime.model,
            "Synthetic coding check v1. Return exactly this one-line Python "
            f"function and nothing else: {expected}",
        ),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    coding = await stream_turn(
        url, coding_payload, prompt_estimate=37, on_flush=lambda _text: None
    )
    assert coding.response_model == runtime.model
    assert coding.finish_reason == "stop"
    assert coding.stream_complete is True
    assert coding.full_text.strip() == expected
    assert coding.first_output_s is not None
    assert coding.answer_started_s is not None
    assert coding.total_s > 0
    assert coding.reasoning_text == ""
    assert coding.tool_calls == ()
    assert coding.accounting.completion_tokens > 0

    reasoning_payload: dict[str, Any] = {
        **_payload(
            runtime.model,
            "Think briefly, then answer with the single word: blue",
            max_tokens=96,
            chat_template_kwargs={"enable_thinking": True},
        ),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    reasoned = await stream_turn(
        url, reasoning_payload, prompt_estimate=30, on_flush=lambda _text: None
    )
    # Pinned behaviour (2026-09-08): thinking consumes the 96-token budget,
    # server ends with finish_reason length and no answer text. Corrected
    # client must preserve reasoning yet refuse success.
    assert reasoned.response_model == runtime.model
    assert reasoned.reasoning_text != ""
    assert reasoned.first_output_s is not None
    assert reasoned.total_s > 0
    if not reasoned.full_text:
        assert reasoned.stream_complete is False
        assert reasoned.answer_started_s is None
    else:
        assert reasoned.answer_started_s is not None
        assert reasoned.first_output_s <= reasoned.answer_started_s
    runtime.evidence.record(
        "corrected_client_stream_turn",
        status="supported",
        coding={
            "full_text": coding.full_text,
            "response_model": coding.response_model,
            "finish_reason": coding.finish_reason,
            "stream_complete": coding.stream_complete,
            "first_output_s": coding.first_output_s,
            "answer_started_s": coding.answer_started_s,
            "total_s": coding.total_s,
            "prompt_tokens": coding.accounting.prompt_tokens,
            "completion_tokens": coding.accounting.completion_tokens,
            "cached_prompt_tokens": coding.cached_prompt_tokens,
        },
        reasoning={
            "reasoning_chars": len(reasoned.reasoning_text),
            "answer_chars": len(reasoned.full_text),
            "response_model": reasoned.response_model,
            "finish_reason": reasoned.finish_reason,
            "stream_complete": reasoned.stream_complete,
            "first_output_s": reasoned.first_output_s,
            "answer_started_s": reasoned.answer_started_s,
            "total_s": reasoned.total_s,
        },
    )
