"""App package: coordinator re-export (entry point `mlx_tui.app:main`)."""

from mlx_tui.app.app import MlxTuiApp
from mlx_tui.app.cli import main

__all__ = ["MlxTuiApp", "main"]
