"""
Bug-fix commit classifier for Guardian's Git History module.

Pure function -- no git I/O, no state. Directly testable with arbitrary
message strings without needing a real repository.
"""

from __future__ import annotations

import re

# Whole-word patterns that indicate a bug-fix commit.  Using \b word boundaries
# prevents false positives from partial matches (e.g. "prefix" contains "fix"
# but should not count).
_BUG_FIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfix(es|ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bbug\b",             re.IGNORECASE),
    re.compile(r"\bhotfix\b",          re.IGNORECASE),
    re.compile(r"\brevert\b",          re.IGNORECASE),
    # Issue tracker references: bare #123, or an explicit known-prefix (GH-, JIRA-).
    # The previous catch-all [A-Z]+-\d+ was too broad — it also matched things like
    # "README-2024" or "CHAPTER-5", which are not issue references.
    re.compile(r"(#|GH-|JIRA-)\d+", re.IGNORECASE),
]


def is_bug_fix_commit(message: str) -> bool:
    """
    Return True if the commit message looks like a bug-fix commit.

    Checks for whole-word occurrences of common bug-fix keywords (fix, bug,
    hotfix, revert) and issue-tracker references (#123, GH-42, JIRA-99).
    Word boundaries prevent partial matches: "prefix" won't match "fix",
    and "debug" won't match "bug".

    This is a pure function with no I/O — it can be tested directly with
    arbitrary message strings without needing a git repository.
    """
    return any(pattern.search(message) for pattern in _BUG_FIX_PATTERNS)
