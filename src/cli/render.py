"""
Rendering for Guardian's CLI -- both output formats read from the exact
same ChangePassport object, never a separately-derived representation.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from src.cli.passport import ChangePassport


_STATUS_WORDS = {"A": "add", "M": "modify", "D": "delete", "R": "rename"}


def render_text(passport: ChangePassport) -> str:
    """
    Render a ChangePassport as the compact human-readable format.

    ONE header for the whole passport, not repeated per file. Each file is
    a short indented block: Risk, Blast radius, Fan-in, Last touch -- no
    bug-fix count or ownership % here, those are Agent-input-only and not
    shown in the default view (still available via the raw evidence if a
    future --verbose flag is added). Findings/checks are batch-level,
    rendered once at the end, since the Agent runs once per call over the
    whole changed-file set -- not once per file.
    """
    lines: list[str] = ["GUARDIAN CHANGE PASSPORT", "─" * 24]

    for fp in passport.files:
        status_word = _STATUS_WORDS.get(fp.status, fp.status)
        lines.append("")
        lines.append(f"File: {fp.path} [{status_word}]")

        if not fp.evidence_available:
            lines.append("    Evidence unavailable — file not in Evidence Store.")
            lines.append("    Run 'guardian scan' first, or this file may have been deleted.")
            continue

        lines.append(f"    Risk: {fp.risk_level} — {fp.risk_score:.1f}/10")
        lines.append(
            f"    Blast radius: {fp.blast_radius_total} dependent files "
            f"({fp.blast_radius_direct} direct, {fp.blast_radius_indirect} indirect)"
        )
        lines.append(f"    Fan-in: {fp.fan_in} files import this")
        last = fp.last_touch_date if fp.last_touch_date else "unknown"
        lines.append(f"    Last touch: {last}")

    lines.append("")
    lines.append("Important findings:")
    if passport.agent_available:
        for finding in passport.agent_findings:
            lines.append(f"  {finding}")
    else:
        lines.append(f"  [Agent unavailable — {passport.agent_error or 'unknown reason'}]")

    lines.append("")
    lines.append("Recommended checks:")
    if passport.agent_available:
        if passport.agent_checks:
            for check in passport.agent_checks:
                lines.append(f"  - {check}")
        else:
            lines.append("  Nothing evidence-based to flag for this change.")
    else:
        lines.append(f"  [Agent unavailable — {passport.agent_error or 'unknown reason'}]")

    return "\n".join(lines)


def render_file_names(passport: ChangePassport) -> str:
    """
    Render just changed file names and their deterministic risk scores,
    sorted for a stable, scannable order. Never touches the Agent -- risk
    scores are already computed and sitting on the passport.
    """
    lines = []
    for fp in sorted(passport.files, key=lambda f: f.path):
        if fp.evidence_available:
            lines.append(f"{fp.path}  [{fp.status}]  {fp.risk_score:.1f}/10")
        else:
            lines.append(f"{fp.path}  [{fp.status}]  (no evidence)")
    return "\n".join(lines)


def render_json(passport: ChangePassport) -> str:
    """Serialize the ChangePassport to JSON. Uses dataclass-to-dict conversion."""
    return json.dumps(asdict(passport), indent=2)
