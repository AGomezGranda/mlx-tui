"""Best-effort local Mac memory observations."""

from __future__ import annotations

import platform
from functools import lru_cache

import psutil

from mlx_tui.catalog.assessment import HardwareProfile, RuntimeCapabilities


@lru_cache(maxsize=1)
def _device_facts() -> tuple[str | None, int | None]:
    chip = None
    working_set = None
    if platform.system() == "Darwin":
        try:
            import mlx.core as mx  # noqa: PLC0415

            info = mx.device_info()
            observed_chip = info.get("device_name")
            observed_working_set = info.get("max_recommended_working_set_size")
            chip = observed_chip if isinstance(observed_chip, str) else None
            working_set = (
                observed_working_set if isinstance(observed_working_set, int) else None
            )
        except Exception:
            pass
    return chip, working_set


def local_hardware() -> HardwareProfile:
    vm = psutil.virtual_memory()
    chip, working_set = _device_facts()
    return HardwareProfile(
        chip,
        vm.total,
        vm.available,
        working_set,
        os_name=platform.system(),
        architecture=platform.machine(),
    )


_CONVENTIONAL_ARCHITECTURES = frozenset(
    {
        "llama",
        "qwen2",
        "qwen3",
        "mistral",
        "gemma",
        "gemma2",
        "gemma3",
        "phi3",
        "phi",
        "deepseek_v2",
        "mixtral",
        "starcoder2",
        "granite",
    }
)


def runtime_capabilities(
    mode: str, *, managed_verified: bool = False
) -> RuntimeCapabilities:
    if mode != "managed" or not managed_verified:
        return RuntimeCapabilities(None, frozenset(), False)
    # The managed runtime is pinned by the app; keep this conservative list
    # separate from whatever mlx-lm happens to be in the TUI environment.
    from mlx_tui.managed.paths import MLX_LM_VERSION  # noqa: PLC0415

    return RuntimeCapabilities(
        f"managed MLX-LM {MLX_LM_VERSION}", _CONVENTIONAL_ARCHITECTURES, True
    )
