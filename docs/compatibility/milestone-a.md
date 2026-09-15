# Milestone A compatibility report

**Test date:** 2026-09-08  
**Machine:** `local-m4-16gib`  
**Result:** MLX-LM candidate accepted for the request/stream subset below

This report records request-scoped evidence. It does not claim current model
residency, engine cancellation, allocator peaks, or decode/prefill speed.

## Reproduction identity

- Hardware: Mac mini `Mac16,10`, Apple M4, arm64, 16 GiB unified memory.
- OS: macOS 26.6.2 (25G83), Darwin 25.6.0.
- TUI revision: `5ce9cc8ab32b4ebf3f1d648cf670dca3b7b3e115`.
- Pre-implementation tracked working-tree diff SHA-256:
  `9a0b2746729a9478c0cb5aa18cb4243502c39792408d5f13dfc8799936ef85fc`.
  The approved plan was executed on that staged/unstaged baseline; Phase 1 files
  are additional untracked work relative to that identity.
- Candidate runtime: MLX-LM 0.32.0 from immutable commit
  `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, with MLX 0.32.2.
- Resolver: `uv 0.12.7`; Python 3.13.1.
- Exact freeze: [`milestone-a-runtime.txt`](milestone-a-runtime.txt).

The isolated environment was created outside the project and operator
environment:

```sh
uv venv --python 3.13 /Users/alvarogomez/.cache/mlx-tui-contract-74e7cf9
uv pip install --python /Users/alvarogomez/.cache/mlx-tui-contract-74e7cf9/bin/python 'mlx-lm @ git+https://github.com/ml-explore/mlx-lm.git@74e7cf931e84ef7c2f63e875adf414e20decc1c5' 'pytest>=9.1.1' 'httpx>=0.28'
uv pip freeze --python /Users/alvarogomez/.cache/mlx-tui-contract-74e7cf9/bin/python
```

## Model and launch

The live contract used:

- Repository: `mlx-community/Qwen3-1.7B-4bit`.
- Immutable revision: `3b1b1768f8f8cf8351c712464f906e86c2b8269e`.
- Local size: 938 MiB; Qwen3 architecture; 4-bit quantization, group size 64.
- Tokenizer: `Qwen2Tokenizer`, repository chat template, `<|im_end|>` EOS.
- Asset SHA-256: `config.json`
  `507a6701220524eb8b283425bf0856a9ae4f21f4052e563896ddd668994b1dc7`;
  `tokenizer_config.json`
  `253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0`;
  `tokenizer.json`
  `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`;
  `model.safetensors`
  `0e86d9677e519323849eac1bc272caae88567a481ff188c431f70be543d9995f`.

Two other immutable, locally available resources were identified but not
live-qualified by this run:

- `mlx-community/Qwen3.5-4B-MLX-4bit@32f3e8ecf65426fc3306969496342d504bfa13f3`
- `ornith-ai/Ornith-1.5-9B-MLX-4bit@a48173b246ac705be75c05bedf1a0666db522d53`

The dedicated operator-launched endpoint was isolated on loopback port 18080:

```sh
/Users/alvarogomez/.cache/mlx-tui-contract-74e7cf9/bin/mlx_lm.server \
  --model /Users/alvarogomez/.cache/huggingface/hub/models--mlx-community--Qwen3-1.7B-4bit/snapshots/3b1b1768f8f8cf8351c712464f906e86c2b8269e \
  --host 127.0.0.1 --port 18080 --log-level INFO
```

The deterministic coding check was version `coding-check-v1`: temperature 0,
seed 7, thinking disabled, 32-token output budget, and the fixed prompt
requesting exactly `def answer(): return 42`. The response matched byte-for-byte
after surrounding whitespace removal. Generated code was not executed.

## Verified surface

| Capability | Library/server exposure | Model compatibility | Verified local effect |
|---|---|---|---|
| `/health` | Exposed separately from `/v1/models` | Model-independent | 200 `{"status":"ok"}` before and after generation |
| Catalogue | Exposed | Lists cached resources and the explicit local target | Three entries; ordering was not treated as selection or residency |
| Non-stream text | Exposed | Compatible | Deterministic coding check stopped normally with exact output |
| Stream terminal | SSE `[DONE]` exposed | Compatible | `[DONE]`, response identity, finish reason and usage observed |
| Reasoning | `delta.reasoning` exposed | Compatible | Non-empty reasoning arrived before completion |
| Structured tools | Server accepts `tools` | Not demonstrated by this model/settings | Model returned JSON-looking text, not `tool_calls`; unavailable |
| Usage | Prompt/completion/total and cached detail exposed | Compatible | Positive integer counts observed |
| Prefix reuse | Cached-token count exposed | Compatible | First/identical/changed cached counts were 3/109/100 of 110 prompt tokens |
| Samplers | temperature/top-p/top-k/min-p accepted | Generation succeeded | Acceptance verified; behavioral effect not established |
| Invalid sampler | Validation exists | Model-independent | `top_p=2` disconnected without an HTTP response; rejected but malformed |
| Missing `model` | Server applies its startup default | Compatible | Request succeeded but response identity was only `default_model` |
| Two requests | Bound of two accepted | Compatible | 0.210 s and 0.381 s individually; 0.381 s aggregate |
| Client disconnect | Connection can close before/during output | Compatible | Follow-up succeeded; engine cancellation remains unknown |
| Failed model/recovery | Invalid absolute target rejected | Compatible after failure | Invalid target returned 404; pinned target then succeeded |

The missing-model behavior is not sufficiently attributable for the TUI to
send an unselected request: `default_model` does not name the configured model.
Milestone A therefore requires explicit selection even on this runtime.

Unsupported or unknown: structured tool output for this model/settings, KV
quantization, allocator peaks, engine prefill timing, current residency, and
engine cancellation. Accepted sampler controls are not claimed to have a
verified behavioral effect. Cached-token usage is server-reported reuse, not
residency evidence.

## Contract command and evidence

Ordinary runs remain model-free:

```sh
uv run pytest -q tests/runtime
```

The live acceptance run used:

```sh
MLX_TUI_CONTRACT_URL=http://127.0.0.1:18080 \
MLX_TUI_CONTRACT_MODEL=/Users/alvarogomez/.cache/huggingface/hub/models--mlx-community--Qwen3-1.7B-4bit/snapshots/3b1b1768f8f8cf8351c712464f906e86c2b8269e \
MLX_TUI_CONTRACT_OUTPUT=/Users/alvarogomez/code/python/mlx-tui/docs/compatibility/evidence/milestone-a \
uv run pytest -q tests/runtime
```

All runs are retained; `contract-v1-20260908T183324Z-480180d2` is the
2026-09-08 corrected-client acceptance run (6 passed: 5 raw HTTP contracts
plus `corrected_client_stream_turn` through `mlx_tui.chat.stream_turn`).
Evidence SHA-256 values:

- Freeze: `d856e423900bb148aa1d8e4a2acf2df14a8d8ae4ad56b86b0f11e525b01397d3`.
- Corrected-client acceptance metadata:
  `4a0736b91574daab76cb32e6b3e564a07618ecdedea02f1ed55e8b974d0b6509`.
- Corrected-client acceptance results:
  `54ef767f23773659eb6730e794eb85cb2795401f56312a27c65886e437b1598a`.
- Prior raw-only acceptance metadata/results (`contract-v1-20260908T171641Z-c1e3bc66`):
  `3ef71ff8c9612a4c0c682a3542ba5ba303dd32c73610f772271b64ad4c8762ca` /
  `c377a2d6d27868cc01800e192c58eef7d7c6a113f84f935baae481fc4d1f1d02`.
- Earlier discovery metadata/results:
  `f160cb96b665dc8acde5f0e02e4ddf19c6569e2b3b571b970eecec71373a08dd` /
  `1ce414b2fc1a1558d43d5c442890d7da3563bd64324f93f4603c1230292c2200`.

Corrected-client revalidation (2026-09-08, same endpoint/model/settings):
coding-check-v1 via `stream_turn` returned exactly
`def answer(): return 42` with response identity equal to the request model,
finish `stop`, `stream_complete` true, first-output/answer 0.10 s, total
0.18 s, prompt 37/completion 8/cached 36. Reasoning via `stream_turn`
(thinking enabled, 96-token budget) preserved 402 reasoning chars with
response identity intact but no answer text, finish `length`,
`stream_complete` false, first output 0.10 s, answer start null, total
1.24 s. The client therefore handles the pinned stop subset as success and
refuses reasoning-only/length-capped output as success, matching the commit
matrix. Supported request/stream subset: non-stream and SSE `[DONE]`
terminated turns with `delta.content`/`delta.reasoning`, indexed tool
fragments (unexecuted), integer usage plus cached detail, request-echoed
`model` identity, and `stop`/`length` finish reasons; premature EOF,
malformed frames, and empty/tool-only outcomes are incomplete by definition.

## Memory tiers and Milestone B

Only `local-m4-16gib` has real evidence. A second named, physically available
memory tier was not available and remains an explicit Milestone B entry
blocker. The two larger cached model revisions are resources, not qualified
recommendations.

Phase 4 rechecked this blocker on 2026-09-09. No second-tier hardware, access,
or owner facts are available to add without invention. The required observation
fields and qualification procedure are tracked in
[`milestone-b.md`](milestone-b.md); this historical blocker remains in force.
