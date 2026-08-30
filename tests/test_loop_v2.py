"""Tests for src/agent/loop.py using a fake chat_fn — no live watsonx needed."""

import unittest
from src.agent.loop import run_agent_loop


def _tool_call(name, args, call_id="call_1"):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": args}}],
    }}]}


def _final(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


class TestAgentLoop(unittest.TestCase):

    def test_executes_tool_then_returns_final_response(self):
        calls = iter([_tool_call("echo", '{"value": 42}'), _final("Done, value was 42.")])
        result = run_agent_loop(lambda m, t: next(calls), [{"role": "user", "content": "go"}],
                                 [], {"echo": lambda value: {"got": value}})
        self.assertEqual(result, "Done, value was 42.")

    def test_tool_result_message_has_matching_call_id(self):
        calls = iter([_tool_call("echo", '{"value": 1}', call_id="abc123"), _final("ok")])
        seen = []

        def chat_fn(messages, tools):
            seen.append(list(messages))
            return next(calls)

        run_agent_loop(chat_fn, [{"role": "user", "content": "go"}], [],
                        {"echo": lambda value: {"got": value}})
        tool_msg = seen[1][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_call_id"], "abc123")
        self.assertIn('"got": 1', tool_msg["content"])

    def test_unknown_tool_returns_error_not_crash(self):
        calls = iter([_tool_call("nonexistent", "{}"), _final("handled")])
        result = run_agent_loop(lambda m, t: next(calls), [{"role": "user", "content": "go"}], [], {})
        self.assertEqual(result, "handled")

    def test_max_turns_exceeded_raises(self):
        with self.assertRaises(RuntimeError):
            run_agent_loop(lambda m, t: _tool_call("echo", "{}"),
                            [{"role": "user", "content": "go"}], [],
                            {"echo": lambda: {}}, max_turns=2)


if __name__ == "__main__":
    unittest.main()
