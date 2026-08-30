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
import warnings
from dataclasses import dataclass, field

from dotenv import load_dotenv

warnings.filterwarnings("ignore", message=".*WatsonxAPIWarning.*")

from src.agent.loop import run_agent_loop
from src.agent.tools import TOOLS, TOOL_FUNCTIONS

load_dotenv()  # no-op if .env doesn't exist; real env vars still take precedence

# The "submit answer" tool. Calling this ends the Agent loop -- its arguments
# ARE the final findings/checks, generated through the same reliable
# tool-argument path as every other tool call, not typed as free-text JSON
# the model might get wrong. See loop.py's terminal_tool parameter.
_SUBMIT_FINDINGS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_findings",
        "description": (
            "Submit your final findings and recommended checks. Call this "
            "exactly once, after you've investigated all changed files with "
            "the other available tools. This ends your turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short, evidence-based findings, one per file or theme.",
                },
                "checks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific checks directly justified by a finding above. "
                        "Empty list if nothing evidence-based warrants a check."
                    ),
                },
            },
            "required": ["findings", "checks"],
        },
    },
}
_SUBMIT_TOOL_NAME = "submit_findings"


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


def _build_messages(
    repo_path: str,
    db_path: str,
    changed_files: list[tuple[str, str]],
) -> list[dict]:
    """
    Build the initial conversation the Agent loop starts from.

    Findings/checks must be scannable in a 3-4 second glance -- short,
    specific, and traceable to real tool data. Checks must follow directly
    from a specific finding, never generic best-practices filler. Empty
    lists are correct when there's genuinely nothing evidence-based to say.
    """
    files_summary = "\n".join(f"- {path} [{status}]" for path, status in changed_files)
    system = (
        "You are Guardian, a code-change risk analyst. You interpret evidence, "
        "you never invent it. Use the available tools to look up real evidence "
        f"for the changed files below (db_path='{db_path}', repo_path='{repo_path}') "
        "before making any claim. If evidence is unavailable for a file, say so "
        "explicitly rather than guessing. Never state a risk score, risk level, "
        "or blast-radius number yourself -- only report what the tools return.\n\n"
        "Keep every finding and every check to ONE short sentence, scannable in "
        "3-4 seconds. No preamble, no filler, no restating the file list.\n\n"
        "Every recommended check must be directly justified by a specific "
        "finding you made -- never generic advice like 'add unit tests' or "
        "'run your linter' that would apply to any file regardless of its "
        "actual evidence. If a check isn't clearly tied to something you "
        "found, omit it.\n\n"
        "If the evidence genuinely gives you nothing worth flagging, return "
        "empty lists for findings and/or checks -- do not pad with filler "
        "just to have something to say.\n\n"
        "When finished investigating, call the submit_findings tool exactly "
        "once with your findings and checks as arguments. Do not describe "
        "your findings as chat text -- submit_findings is the only way to "
        "give your final answer.\n\n"
        "Each finding/check must be a complete, standalone statement — "
        "never a section header, transition phrase (e.g. 'Checks to perform:'), "
        "or numbering. Write plain text only, no markdown (no **, no numbered "
        "lists) — the CLI renders these as plain strings, not formatted text.\n\n"
        "Findings: write in natural, flowing prose, like briefing a colleague — "
        "not terse bullet fragments. Combine related facts into complete "
        "sentences (e.g. 'The file was newly added with a low risk score of "
        "2.22 and no dependents, so the blast radius is minimal' rather than "
        "three separate fragments for the same file). Be concise — one to "
        "three sentences per finding is plenty. State only what the evidence "
        "actually shows.\n\n"
        "Checks: only recommend a check if something SPECIFIC about this file's "
        "evidence justifies it — e.g. high blast radius means verify dependents, "
        "high bug-fix count means check for a recurring root cause, low ownership "
        "means get a second reviewer. Do not list generic best practices (write "
        "tests, run a linter, update docs, get a code review) that would apply to "
        "any change regardless of its evidence — those aren't Guardian's job to "
        "say. If nothing in the evidence justifies a specific check, return an "
        "empty checks list. An empty list is a correct answer, not a failure."
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
    findings, checks = data["findings"], data["checks"]
    # Must actually be lists, not e.g. a plain string -- list("some string")
    # silently splits into individual characters, which looked like real
    # output but was actually corrupted data. Reject the whole response
    # rather than salvage a wrong-shaped value.
    if not isinstance(findings, list) or not isinstance(checks, list):
        return None
    if not all(isinstance(item, str) for item in findings + checks):
        return None
    return data


def _retry_as_json(chat_fn, previous_raw: str | None) -> str | None:
    """
    One repair attempt: ask the model to reformat its own previous answer as
    strict JSON, without re-running tool calls or adding new information.
    Returns None on any failure -- caller falls back to unavailable exactly
    as if no retry had happened.
    """
    retry_messages = [
        {
            "role": "system",
            "content": (
                'Reformat the following answer as ONLY a valid JSON object of '
                'this exact shape, no other text, no markdown: '
                '{"findings": ["..."], "checks": ["..."]} '
                "Fix any bracket/quote mistakes. Do not add, remove, or change "
                "any information -- only fix the formatting."
            ),
        },
        {"role": "user", "content": previous_raw or ""},
    ]
    try:
        response = chat_fn(retry_messages, [])
        return response["choices"][0]["message"].get("content")
    except Exception:
        return None


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
    all_tools = TOOLS + [_SUBMIT_FINDINGS_TOOL]
    try:
        raw = run_agent_loop(
            chat_fn, messages, all_tools, TOOL_FUNCTIONS,
            terminal_tool=_SUBMIT_TOOL_NAME,
        )
    except Exception as exc:
        return AgentResult(available=False, error=f"Agent loop failed: {exc}")

    parsed = _parse_agent_json(raw)
    if parsed is None:
        # granite-4-h-small isn't fully reliable at strict JSON-only output --
        # give it one chance to reformat its own answer, without re-running
        # any tool calls or adding new content. If this also fails, fall
        # back to unavailable exactly as before -- never guess at broken JSON.
        raw = _retry_as_json(chat_fn, raw)
        parsed = _parse_agent_json(raw)

    if parsed is None:
        import sys
        print(f"[DEBUG] Agent response could not be parsed as JSON:\n{raw!r}\n", file=sys.stderr)
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
        return model.chat(messages=messages, tools=tools, params={"max_tokens": 1024})

    return _run_with_chat_fn(chat_fn, repo_path, db_path, changed_files)
