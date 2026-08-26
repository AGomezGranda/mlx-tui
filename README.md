# mlx tui

A one-screen terminal client for a local MLX OpenAI-compatible server
(`mlx_lm.server` / `mlx_vlm.server`): an always-visible status bar (liveness,
loaded model, RSS, available memory) plus a streaming chat pane with per-turn
TTFT / tok-s instrumentation.

## Run

```
uv sync
uv run mlx-tui --port 8080
```

Start a server first:

```
uv tool install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-1.7B-8bit --port 8080

or

mlx_lm.server --model ornith-ai/Ornith-1.5-9B-MLX-4bit --port 8080
```

`ctrl+q` quits; `esc` cancels an in-flight chat turn.
