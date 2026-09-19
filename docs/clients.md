# Local clients

Pointing another local client at the same server is unqualified local use,
not a supported shared-serving setup.

## Endpoint preview

Press `F3` (`Endpoint`) in the TUI for a fresh snapshot of the current
endpoint. It shows the `/v1` base URL, the `/v1/chat/completions` chat URL,
the selected request model, the last response model, reachability, and a
curl recipe with the exact request shape. Copy values from that preview at
the time of use; do not hand-write URLs or model names.

## HTTP request shape

Point any HTTP client at the loopback `/v1` base from the preview. The
preview's request shape is:

```python
import httpx

payload = {
    "model": "<selected request model from the preview>",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 32,
    "stream": False,
}
response = httpx.post(
    "http://<host>:<port>/v1/chat/completions",
    json=payload,
    timeout=httpx.Timeout(120.0, connect=5.0),
)
```

The preview also shows a generated curl recipe with a 30-second cap. Copying
either example executes nothing by itself and carries no support claim.

## OpenCode

The configuration format differs between OpenCode releases. The legacy
`provider`/`npm` format below was tested with OpenCode **1.18.31**. Check the
[OpenCode provider documentation](https://opencode.ai/docs/providers) for the
format supported by your installed version.

For OpenCode 1.x, use the exact model ID returned by `GET /v1/models` as the
key under `models`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "mlx-tui-local": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://127.0.0.1:8080/v1"
      },
      "models": {
        "mlx-community/Qwen3-1.7B-4bit": {
          "name": "Qwen3 1.7B MLX"
        }
      }
    }
  }
}
```

Replace `mlx-community/Qwen3-1.7B-4bit` with the selected model's actual ID.
The `models` key is sent to the MLX server; `name` is only a display label.
Do not leave the key as `local` while putting the real model ID only in
`name`. That causes the server to resolve a Hugging Face repository literally
named `local`, resulting in an error such as:

```text
https://huggingface.co/api/models/local/revision/main
```

This is a model-ID mismatch, not evidence that the locally cached model is
missing or that Hugging Face authentication is required. The `baseURL` must
point to the MLX server's `/v1` endpoint, not to the TUI itself.

Newer OpenCode releases may use the `providers`/`package` configuration and a
separate `modelID` field. In that format, the selectable model key may be an
alias, but `modelID` must contain the exact ID returned by the MLX server. See
the [newer OpenCode provider reference](https://opencode.ai/v2/docs/providers)
before copying that syntax into a different OpenCode version.

## Limitations

- There is no cross-client model or lifecycle coordination. An external
  client may change the server target; the TUI cannot detect all external
  work and promises no drain.
- The server accepts per-request model selection, so a request for another
  model can replace the loaded target. Wrong-target rejection is not
  enforceable.
- Managed shutdown stops its owned child; attach shutdown never stops the
  operator's server. An intentional shutdown during client activity is an
  interruption, not a graceful drain.
- Health and catalogue responses never prove which model is resident.
- Only plain local HTTP on a loopback endpoint is described here; TLS, API
  keys, remote hosts, and reverse-proxy base paths are not supported.
