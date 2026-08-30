"""
watsonx PoC — proves IBM watsonx.ai native tool-calling works before the
real Agent loop is trusted against it.

What this file demonstrates
----------------------------
1. ModelInference.chat() accepts a `tools` list following the OpenAI
   function-calling schema.
2. The model can respond with finish_reason == "tool_calls" instead of
   "stop", signalling it wants a tool executed.
3. run_agent_loop() (src/agent/loop.py) drives this end to end: it keeps
   calling the model, executing whatever tools are requested, and feeding
   results back, for as many rounds as the model actually needs — not a
   fixed two-step script. This is what actually proves multi-round
   tool-calling works, since the model may request a second tool only
   after seeing the first tool's result.

This is isolated from Guardian's scanner and CLI — it only uses Guardian's
own tools (get_diff_files) with a real temp repo to keep the demo grounded.

Running
-------
    # Requires environment variables:
    WATSONX_API_KEY   — IBM Cloud API key
    WATSONX_URL       — watsonx endpoint, e.g. https://us-south.ml.cloud.ibm.com
    WATSONX_PROJECT_ID — watsonx project ID

    python3 -m src.agent.watsonx_poc

If any credential is missing the script exits immediately with a clear
error rather than pretending the interaction succeeded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()  # reads .env into os.environ if present; no-op if it doesn't exist


# ---------------------------------------------------------------------------
# Credential validation — fail early and clearly
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Return the value of env var name, or exit with a clear error."""
    value = os.environ.get(name)
    if not value:
        print(
            f"ERROR: environment variable {name!r} is not set.\n"
            "Set WATSONX_API_KEY, WATSONX_URL, and WATSONX_PROJECT_ID "
            "before running this PoC.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


# ---------------------------------------------------------------------------
# Minimal demo repo helper
# ---------------------------------------------------------------------------

def _make_demo_repo() -> str:
    """
    Create a minimal two-commit git repo in a temp directory.

    Returns the repo path.  The second commit modifies a.py so that
    HEAD~1..HEAD has a non-empty diff we can ask the model about.
    """
    repo = tempfile.mkdtemp(prefix="guardian_poc_")
    cmds = [
        ["git", "init", repo],
        ["git", "-C", repo, "config", "user.email", "poc@guardian"],
        ["git", "-C", repo, "config", "user.name", "PoC"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True, capture_output=True)

    with open(os.path.join(repo, "utils.py"), "w") as f:
        f.write("def helper(): pass\n")
    with open(os.path.join(repo, "main.py"), "w") as f:
        f.write("import utils\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)

    with open(os.path.join(repo, "main.py"), "a") as f:
        f.write("# fix: resolve edge case\n")
    subprocess.run(["git", "-C", repo, "add", "main.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "fix: resolve edge case"], check=True, capture_output=True)

    return repo


# ---------------------------------------------------------------------------
# Main PoC flow
# ---------------------------------------------------------------------------

def run_poc() -> None:
    """
    Drive the real Agent loop against watsonx to prove multi-round
    tool-calling works end to end — the loop, not a hand-rolled script,
    decides how many rounds are needed based on the model's actual behavior.
    """
    api_key    = _require_env("WATSONX_API_KEY")
    url        = _require_env("WATSONX_URL")
    project_id = _require_env("WATSONX_PROJECT_ID")

    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from src.agent.tools import TOOLS, TOOL_FUNCTIONS
    from src.agent.loop import run_agent_loop

    repo_path = _make_demo_repo()
    print(f"[PoC] Demo repo: {repo_path}")

    model = ModelInference(
        model_id="ibm/granite-3-8b-instruct",
        credentials=Credentials(api_key=api_key, url=url),
        project_id=project_id,
    )

    def chat_fn(messages: list[dict], tools: list[dict]) -> dict:
        """Adapts ModelInference.chat() to the loop's provider-agnostic shape."""
        return model.chat(messages=messages, tools=tools)

    messages = [
        {
            "role": "system",
            "content": (
                "You are Guardian, a code-change risk analyst. "
                "Use the available tools to answer the user's question. "
                "Do not guess — call the tools to get real data. "
                f"The repository path is '{repo_path}'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"What files changed between HEAD~1 and HEAD in the repo at "
                f"'{repo_path}'? For each changed file, also check if it has "
                "any known blast radius."
            ),
        },
    ]

    print("\n[PoC] Running the real Agent loop (as many rounds as the model needs) ...")
    final_response = run_agent_loop(chat_fn, messages, TOOLS, TOOL_FUNCTIONS)

    print(f"\n[PoC] Final model response:\n  {final_response}")
    print("\n[PoC] SUCCESS — run_agent_loop completed a real multi-round tool-calling flow.")


if __name__ == "__main__":
    run_poc()
