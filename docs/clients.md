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
