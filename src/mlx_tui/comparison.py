"""Stable comparison import surface.

The headless implementation lives in focused sibling modules; this module
remains the public compatibility facade used by the app, UI code, and
runtime qualification.
"""

from __future__ import annotations

from mlx_tui.comparison_contracts import (
    CODING_CHECK_EXPECTED,
    CODING_CHECK_PROMPT,
    RSS_SCOPE,
    TRIAL_REPEATS,
    TRIAL_SLOTS,
    ComparisonIdentityError,
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonStatus,
    ComparisonValidationError,
    DecisionKind,
    JSONValue,
    LoopbackEndpoint,
    MemorySample,
    SavedChoice,
    TrialResult,
    TrialState,
    parse_loopback_url,
)
from mlx_tui.comparison_persistence import (
    choice_is_committed,
    choice_path,
    commit_choice,
    comparison_dir,
    decode_comparison,
    encode_comparison,
    load_choice,
    load_comparison,
    save_choice,
    save_comparison,
)
from mlx_tui.comparison_runner import (
    assert_coding_check_v1,
    coding_check_v1,
    coding_payload,
    run_comparison,
    verify_profile_snapshot,
)

__all__ = [
    "CODING_CHECK_EXPECTED",
    "CODING_CHECK_PROMPT",
    "RSS_SCOPE",
    "TRIAL_REPEATS",
    "TRIAL_SLOTS",
    "ComparisonIdentityError",
    "ComparisonInput",
    "ComparisonPersistenceError",
    "ComparisonResult",
    "ComparisonStatus",
    "ComparisonValidationError",
    "DecisionKind",
    "JSONValue",
    "LoopbackEndpoint",
    "MemorySample",
    "SavedChoice",
    "TrialResult",
    "TrialState",
    "assert_coding_check_v1",
    "choice_is_committed",
    "choice_path",
    "coding_check_v1",
    "coding_payload",
    "commit_choice",
    "comparison_dir",
    "decode_comparison",
    "encode_comparison",
    "load_choice",
    "load_comparison",
    "parse_loopback_url",
    "run_comparison",
    "save_choice",
    "save_comparison",
    "verify_profile_snapshot",
]
