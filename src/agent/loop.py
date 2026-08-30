"""
Provider-agnostic Agent orchestration loop for Guardian.

This loop knows nothing about watsonx, OpenAI, or any specific provider. It
operates on a `chat_fn` callable supplied by the caller — chat_fn(messages,
tools) -> response dict, in the OpenAI-compatible shape confirmed by
watsonx_poc.py: response["choices"][0]["message"], with
finish_reason == "tool_calls" signalling the model wants a tool executed.

This is what makes the loop provider-agnostic: swapping providers means
swapping chat_fn, never touching this file.

The Agent never computes evidence itself — every tool call here dispatches
to a function in TOOL_FUNCTIONS (src/agent/tools.py), which only ever reads
from the deterministic Evidence Store / Git History / Risk Scorer. This loop
only relays messages and results; it must never fabricate a tool result.

terminal_tool support: a caller may designate one tool name as the "submit
the final answer" tool (e.g. submit_findings). Calling it ends the loop
immediately with its raw arguments string, rather than continuing until the
model produces free-text content. This exists because free-text "please
reply with only JSON" is far less reliable than a tool call's arguments,
which the provider already generates through a more constrained path.
"""

from __future__ import annotations

import json
from typing import Callable

ChatFn = Callable[[list[dict], list[dict]], dict]


def run_agent_loop(
    chat_fn: ChatFn,
    messages: list[dict],
    tools: list[dict],
    tool_functions: dict[str, Callable],
    max_turns: int = 10,
    terminal_tool: str | None = None,
) -> str | None:
    """
    Run the tool-calling loop until the model returns a final (non-tool-call)
    response, calls terminal_tool (if set), or max_turns is exceeded.

    messages       — initial conversation (system/user messages)
    tools          — tool definitions passed to chat_fn each turn
    tool_functions — name -> callable, dispatch table for executing tool calls
    max_turns      — safety cap; raises RuntimeError if never reached a final
                     response, rather than looping forever on a confused model
    terminal_tool  — if set, calling this tool name ends the loop immediately;
                     its arguments (raw JSON string, unparsed) are returned
                     instead of executing it via tool_functions. Any other
                     tool calls in the same turn are ignored once found.

    Returns the final message content (str), the terminal tool's raw
    arguments string, or None if the model's final message had no content.
    """
    conversation = list(messages)  # never mutate the caller's list

    for _ in range(max_turns):
        response = chat_fn(conversation, tools)
        choice = response["choices"][0]
        message = choice["message"]

        if choice.get("finish_reason") != "tool_calls":
            return message.get("content")

        tool_calls = message.get("tool_calls", [])
        if terminal_tool is not None:
            for tool_call in tool_calls:
                if tool_call["function"]["name"] == terminal_tool:
                    return tool_call["function"]["arguments"]

        conversation.append(message)
        for tool_call in tool_calls:
            conversation.append(_execute_tool_call(tool_call, tool_functions))

    raise RuntimeError(
        f"Agent loop exceeded max_turns={max_turns} without a final response "
        "— the model may be stuck requesting tools repeatedly."
    )


def _execute_tool_call(tool_call: dict, tool_functions: dict[str, Callable]) -> dict:
    """
    Execute one tool call and return the "tool" role message to append to
    the conversation. Never lets an exception escape — a failing tool
    becomes an error result the model can see, not a crash.
    """
    name = tool_call["function"]["name"]
    fn = tool_functions.get(name)

    if fn is None:
        result = {"error": f"Unknown tool: {name}"}
    else:
        try:
            args = json.loads(tool_call["function"]["arguments"])
            result = fn(**args)
        except Exception as exc:
            result = {"error": f"Tool '{name}' failed: {exc}"}

    return {
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "content": json.dumps(result),
    }
