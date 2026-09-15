"""Opt-in fixtures for contracts against an operator-launched MLX-LM server."""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass
class EvidenceRecorder:
    run_dir: Path
    results: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, contract: str, **details: Any) -> None:
        with self._lock:
            self.results.append({"contract": contract, **details})

    def write(self) -> None:
        path = self.run_dir / "results.json"
        path.write_text(
            json.dumps(
                {"schema_version": 1, "contracts": self.results},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


@dataclass(frozen=True)
class ContractRuntime:
    base_url: str
    model: str
    evidence: EvidenceRecorder


@pytest.fixture(scope="session")
def contract_runtime() -> Iterator[ContractRuntime]:
    raw_url = os.environ.get("MLX_TUI_CONTRACT_URL")
    model = os.environ.get("MLX_TUI_CONTRACT_MODEL")
    if not raw_url or not model:
        pytest.skip(
            "real MLX contracts require MLX_TUI_CONTRACT_URL and MLX_TUI_CONTRACT_MODEL"
        )

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
        pytest.fail("MLX_TUI_CONTRACT_URL must be an HTTP(S) loopback endpoint")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        pytest.fail("MLX_TUI_CONTRACT_URL must be a plain endpoint URL")

    output_root = Path(
        os.environ.get("MLX_TUI_CONTRACT_OUTPUT", ".runtime-evidence/milestone-a")
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"contract-v1-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "endpoint": raw_url.rstrip("/"),
        "model": model,
        "python": sys.version,
        "platform": platform.platform(),
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    recorder = EvidenceRecorder(run_dir)
    runtime = ContractRuntime(raw_url.rstrip("/"), model, recorder)
    yield runtime
    recorder.write()
