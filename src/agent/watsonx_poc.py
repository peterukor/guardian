"""
watsonx PoC — proves IBM watsonx.ai native tool-calling works before the
real Agent loop is built against it.

What this file demonstrates
----------------------------
1. ModelInference.chat() accepts a `tools` list following the OpenAI
   function-calling schema.
2. The model can respond with finish_reason == "tool_calls" instead of
   "stop", signalling it wants a tool executed.
3. We execute the tool, append a "tool" role message with the result, and
   re-send the conversation.
4. The model receives the tool result and produces a final natural-language
   response (finish_reason == "stop").

This is the minimal multi-step loop the real Agent loop must support.
It is isolated from Guardian's scanner and CLI — it only uses one Guardian
tool (get_diff_files) with a real temp repo to keep the demo grounded.

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

import json
import os
import subprocess
import sys
import tempfile


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
# Tool execution helper (identical logic to what loop.py will use)
# ---------------------------------------------------------------------------

def _execute_tool(tool_call: dict, repo_path: str) -> str:
    """
    Execute a single tool call and return the result as a JSON string.

    tool_call follows the OpenAI/watsonx tool_calls item shape:
        {"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}
    """
    from src.agent.tools import TOOL_FUNCTIONS

    name = tool_call["function"]["name"]
    args = json.loads(tool_call["function"]["arguments"])

    # Inject repo_path for get_diff_files if the model omitted it or used a
    # placeholder — keeps the PoC working without the model guessing a real path.
    if name == "get_diff_files" and not args.get("repo_path"):
        args["repo_path"] = repo_path

    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    return json.dumps(fn(**args))


# ---------------------------------------------------------------------------
# Main PoC flow
# ---------------------------------------------------------------------------

def run_poc() -> None:
    """
    Execute the full multi-step tool-calling demonstration with watsonx.

    Step 1: send user question + tools to the model.
    Step 2: model requests get_diff_files tool → execute it.
    Step 3: send tool result back → model produces final text response.
    """
    api_key    = _require_env("WATSONX_API_KEY")
    url        = _require_env("WATSONX_URL")
    project_id = _require_env("WATSONX_PROJECT_ID")

    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from src.agent.tools import TOOLS

    repo_path = _make_demo_repo()
    print(f"[PoC] Demo repo: {repo_path}")

    model = ModelInference(
        model_id="ibm/granite-3-8b-instruct",
        credentials=Credentials(api_key=api_key, url=url),
        project_id=project_id,
    )

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are Guardian, a code-change risk analyst. "
                "Use the available tools to answer the user's question. "
                "Do not guess — call the tools to get real data."
            ),
        },
        {
            "role": "user",
            "content": (
                f"What files changed between HEAD~1 and HEAD in the repo at "
                f"'{repo_path}'?"
            ),
        },
    ]

    print("\n[PoC] Step 1 — sending initial message to model ...")
    response = model.chat(messages=messages, tools=TOOLS)
    choice = response["choices"][0]
    print(f"  finish_reason: {choice['finish_reason']}")

    if choice["finish_reason"] != "tool_calls":
        print("[PoC] Model did not request a tool call — PoC cannot demonstrate multi-step flow.")
        print("  Final response:", choice["message"].get("content"))
        return

    # Append the assistant's tool-call message to the conversation.
    messages.append(choice["message"])

    for tc in choice["message"]["tool_calls"]:
        name = tc["function"]["name"]
        print(f"\n[PoC] Step 2 — model requested tool: {name}")
        print(f"  arguments: {tc['function']['arguments']}")

        result = _execute_tool(tc, repo_path)
        print(f"  result: {result}")

        # Tool result message — role "tool" with matching tool_call_id.
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    print("\n[PoC] Step 3 — sending tool results back to model ...")
    response2 = model.chat(messages=messages, tools=TOOLS)
    choice2 = response2["choices"][0]
    print(f"  finish_reason: {choice2['finish_reason']}")
    print(f"\n[PoC] Final model response:\n  {choice2['message']['content']}")
    print("\n[PoC] SUCCESS — multi-step tool-calling flow verified.")


if __name__ == "__main__":
    run_poc()
