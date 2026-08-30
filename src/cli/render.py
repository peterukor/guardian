"""
Rendering for Guardian's CLI -- both output formats read from the exact
same ChangePassport object, never a separately-derived representation.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from src.cli.passport import ChangePassport


def render_text(passport: ChangePassport) -> str:
    """
    Render a ChangePassport as the human-readable format specified in AGENTS.md.

    One block per changed file.  Files with no evidence get an explicit
    'Evidence unavailable' notice rather than zeros.  Agent findings/checks
    are rendered once, after all file blocks, since the Agent runs once per
    call over the whole changed-file set -- not once per file.
    """
    lines: list[str] = []
    for fp in passport.files:
        lines.append("\nGUARDIAN CHANGE PASSPORT")
        lines.append("─" * 24)

        if not fp.evidence_available:
            lines.append(f"File: {fp.path}  [{fp.status}]")
            lines.append("Evidence unavailable — file not in Evidence Store.")
            lines.append("Run 'guardian scan' first, or this file may have been deleted.")
            continue

        lines.append(f"Risk: {fp.risk_level} — {fp.risk_score:.1f}/10")
        lines.append("")
        lines.append(f"Changed files: {fp.path}  [{fp.status}]")
        lines.append(
            f"Blast radius: {fp.blast_radius_total} dependent files "
            f"({fp.blast_radius_direct} direct, {fp.blast_radius_indirect} indirect)"
        )
        lines.append("")
        lines.append("Risk factors:")
        lines.append(f"  Fan-in: {fp.fan_in}")
        lines.append(f"  Bug-fix commits: {fp.bug_fix_count}")
        top_pct = fp.top_author_pct or 0.0
        lines.append(f"  Top author concentration: {top_pct * 100:.0f}%")
        last = fp.last_touch_date if fp.last_touch_date else "unknown"
        lines.append(f"  Last touch: {last}")

    lines.append("")
    lines.append("Important findings:")
    if passport.agent_available:
        for finding in passport.agent_findings:
            lines.append(f"  - {finding}")
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


def render_json(passport: ChangePassport) -> str:
    """Serialize the ChangePassport to JSON. Uses dataclass-to-dict conversion."""
    return json.dumps(asdict(passport), indent=2)
