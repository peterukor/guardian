"""
Agent -> Change Passport integration for Guardian.

Runs the Agent exactly once per `analyze` invocation, given the full list of
changed files -- never once per file. The Agent uses its own tools
(get_file_evidence, get_file_blast_radius) during its own loop to fetch
whatever per-file detail it needs; the CLI never pre-fetches evidence into
one giant prompt.

Credential handling is deliberately isolated here: if watsonx credentials
are missing, only this module's result comes back "unavailable" -- the
caller (cli.py) still has a fully working deterministic passport regardless.
Guardian's deterministic output must never depend on live AI credentials
being present.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from src.agent.loop import run_agent_loop
from src.agent.tools import TOOLS, TOOL_FUNCTIONS

load_dotenv()  # no-op if .env doesn't exist; real env vars still take precedence


@dataclass
class AgentResult:
    """
    Result of one Agent invocation for a Change Passport.

    available=False means no findings/checks could be produced (missing
    credentials, a loop failure, or an unparseable response) -- never a
    signal to fabricate content instead.
    """
    available: bool
    findings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    error: str | None = None


def _get_credentials() -> tuple[str, str, str] | None:
    """Return (api_key, url, project_id) or None if any are missing."""
    api_key = os.environ.get("WATSONX_API_KEY")
    url = os.environ.get("WATSONX_URL")
    project_id = os.environ.get("WATSONX_PROJECT_ID")
    if not (api_key and url and project_id):
        return None
    return api_key, url, project_id


def _build_messages(repo_path: str, db_path: str, changed_files: list[tuple[str, str]]) -> list[dict]:
    """Build the initial conversation the Agent loop starts from."""
    files_summary = "\n".join(f"- {path} [{status}]" for path, status in changed_files)
    system = (
        "You are Guardian, a code-change risk analyst. You interpret evidence, "
        "you never invent it. Use the available tools to look up real evidence "
        f"for the changed files below (db_path='{db_path}', repo_path='{repo_path}') "
        "before making any claim. If evidence is unavailable for a file, say so "
        "explicitly rather than guessing. Never state a risk score, risk level, "
        "or blast-radius number yourself -- only report what the tools return.\n\n"
        'When finished investigating, respond with ONLY a JSON object of this '
        'exact shape, no other text: {"findings": ["..."], "checks": ["..."]}'
    )
    user = f"Changed files:\n{files_summary}\n\nProvide findings and recommended checks."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_agent_json(text: str | None) -> dict | None:
    """
    Parse the Agent's final response as {"findings": [...], "checks": [...]}.
    Tolerates a markdown code fence around the JSON. Returns None on any
    parse failure or unexpected shape -- callers must treat that as
    "unavailable", never guess at a partial split.
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or "findings" not in data or "checks" not in data:
        return None
    return data


def _run_with_chat_fn(
    chat_fn,
    repo_path: str,
    db_path: str,
    changed_files: list[tuple[str, str]],
) -> AgentResult:
    """
    Core logic, independent of watsonx setup -- takes any chat_fn matching
    run_agent_loop's interface. This is what tests exercise directly with a
    fake model, without needing real credentials or the ibm_watsonx_ai package.
    """
    messages = _build_messages(repo_path, db_path, changed_files)
    try:
        raw = run_agent_loop(chat_fn, messages, TOOLS, TOOL_FUNCTIONS)
    except Exception as exc:
        return AgentResult(available=False, error=f"Agent loop failed: {exc}")

    parsed = _parse_agent_json(raw)
    if parsed is None:
        return AgentResult(
            available=False,
            error="Agent output unavailable — could not parse a structured response.",
        )

    return AgentResult(
        available=True,
        findings=list(parsed.get("findings", [])),
        checks=list(parsed.get("checks", [])),
    )


def generate_agent_findings(
    repo_path: str,
    db_path: str,
    changed_files: list[tuple[str, str]],
) -> AgentResult:
    """
    Public entry point. Returns an unavailable AgentResult immediately if
    credentials are missing -- never attempts a network call in that case,
    and never fabricates findings as a fallback.
    """
    creds = _get_credentials()
    if creds is None:
        return AgentResult(
            available=False,
            error="WATSONX_API_KEY/WATSONX_URL/WATSONX_PROJECT_ID not configured.",
        )
    api_key, url, project_id = creds

    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference

    try:
        model = ModelInference(
            model_id="ibm/granite-4-h-small",
            credentials=Credentials(api_key=api_key, url=url),
            project_id=project_id,
        )
    except Exception as exc:
        return AgentResult(
            available=False,
            error=f"could not initialize watsonx client: {exc}",
        )

    def chat_fn(messages: list[dict], tools: list[dict]) -> dict:
        return model.chat(messages=messages, tools=tools)

    return _run_with_chat_fn(chat_fn, repo_path, db_path, changed_files)
