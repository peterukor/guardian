"""
Rendering for Guardian's CLI -- both output formats read from the exact
same ChangePassport object, never a separately-derived representation.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from src.cli.passport import ChangePassport


_STATUS_WORDS = {"A": "add", "M": "modify", "D": "delete", "R": "rename"}


def _risk_sort_key(fp) -> float:
    """Sort by risk score descending; files with no evidence sort last."""
    return fp.risk_score if fp.evidence_available and fp.risk_score is not None else -1.0


def render_text(passport: ChangePassport, top_n: int = 3) -> str:
    """
    Render a ChangePassport as the compact human-readable format.

    ONE header for the whole passport, not repeated per file. Files are
    sorted by risk score (highest first) so the top_n shown in full detail
    are genuinely the riskiest ones -- not whatever order git happened to
    report them in. Files beyond top_n are condensed to a single line each.
    This is a rendering choice only -- the Agent still investigates every
    changed file via its own tools regardless of what's condensed here.
    """
    ordered = sorted(passport.files, key=_risk_sort_key, reverse=True)
    shown, condensed = ordered[:top_n], ordered[top_n:]

    lines: list[str] = ["GUARDIAN CHANGE PASSPORT", "─" * 24]

    for fp in shown:
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

    if condensed:
        lines.append("")
        for fp in condensed:
            status_word = _STATUS_WORDS.get(fp.status, fp.status)
            if fp.evidence_available:
                lines.append(f"  {fp.path} [{status_word}] — {fp.risk_level} {fp.risk_score:.1f}/10")
            else:
                lines.append(f"  {fp.path} [{status_word}] — (no evidence)")
        lines.append(f"\nRun with -n {len(passport.files)} to see all files in full detail.")

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
    sorted for a stable, scannable order, with columns aligned to the
    widest entry in this batch. Never touches the Agent -- risk scores
    are already computed and sitting on the passport.
    """
    ordered = sorted(passport.files, key=lambda f: f.path)
    if not ordered:
        return ""

    status_words = [_STATUS_WORDS.get(fp.status, fp.status) for fp in ordered]
    path_width = max(len(fp.path) for fp in ordered)
    bracket_width = max(len(f"[{w}]") for w in status_words)

    lines = []
    for fp, status_word in zip(ordered, status_words):
        bracket = f"[{status_word}]"
        detail = f"Risk: {fp.risk_score:.1f}/10" if fp.evidence_available else "(no evidence)"
        lines.append(f"{fp.path:<{path_width}}  {bracket:<{bracket_width}}  {detail}")
    return "\n".join(lines)


def render_json(passport: ChangePassport) -> str:
    """Serialize the ChangePassport to JSON. Uses dataclass-to-dict conversion."""
    return json.dumps(asdict(passport), indent=2)
