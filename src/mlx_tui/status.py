"""Status-bar domain logic: liveness classification, cold tracking, rendering."""

from __future__ import annotations

from typing import NamedTuple


class MemorySnapshot(NamedTuple):
    avail_gib: float
    total_gib: float


_STATUS_COLOURS = {"green": "green", "amber": "yellow", "red": "red"}

_HTTP_OK = 200


def classify_liveness(status_code: int, body: object) -> str:
    """Pure classifier for a completed ``GET /v1/models`` response.

    'red' is reserved for transport failure and is decided by the caller's
    exception path — this function never sees one. green ⇔ 200 AND body parses
    to a dict whose ``data`` is a non-empty list; every other complete HTTP
    response (junk JSON, wrong shape, 502 HTML proxy squatting on the port)
    is 'amber'.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if status_code == _HTTP_OK and isinstance(data, list) and len(data) > 0:
        return "green"
    return "amber"


class ColdTracker:
    """Tracks whether a turn deserves the ``· cold`` stamp.

    Arming rule: a cold stamp is justified **only** by a true
    green→red→green cycle — a green must have been observed before the red —
    so the first turn after TUI startup against an hours-warm server is never
    mislabelled cold.
    """

    def __init__(self) -> None:
        self.ever_green = False
        self.red_since_green = False
        self.cold_pending = False

    def observe(self, state: str) -> None:
        if state == "green":
            self.ever_green = True
            if self.red_since_green:
                self.cold_pending = True
                self.red_since_green = False
        elif state == "red":
            if self.ever_green:
                self.red_since_green = True

    def consume_cold(self) -> bool:
        """Return whether a cold stamp is armed, resetting it in the same breath."""
        cold = self.cold_pending
        self.cold_pending = False
        return cold


def format_status_line(
    *,
    state: str,
    model: str | None,
    rss_gib: float | None,
    memory: MemorySnapshot,
    port: int,
) -> str:
    """Render the status bar line; em-dashes stand in for missing pieces."""
    colour = _STATUS_COLOURS[state]
    model_part = model if model else "—"
    rss_part = f"{rss_gib:.1f}" if rss_gib is not None else "—"
    return (
        f"[{colour}]●[/] {model_part} · RSS {rss_part} GB · "
        f"avail {memory.avail_gib:.1f}/{memory.total_gib:.1f} GB · :{port}"
    )
