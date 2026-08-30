"""
Unit tests for src/agent/ -- the tool-calling loop, individual tools, and the
Agent-to-passport integration layer. No live watsonx credentials needed; the
loop is driven with a fake chat_fn matching its OpenAI-compatible interface.
"""

import json
import os
import subprocess
import tempfile
import unittest

from src.agent.integration import _run_with_chat_fn, generate_agent_findings
from src.agent.loop import run_agent_loop
from src.agent.tools import get_diff_files, get_file_blast_radius, get_file_evidence
from src.scanner import run_scan


def _tool_call(name, args, call_id="call_1"):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": args}}]}}]}


def _final(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


class TestAgentLoop(unittest.TestCase):

    def test_executes_tool_then_returns_final_response_with_matching_call_id(self):
        calls = iter([_tool_call("echo", '{"value": 1}', call_id="abc123"), _final("Done")])
        seen = []

        def chat_fn(messages, tools):
            seen.append(list(messages))
            return next(calls)

        result = run_agent_loop(chat_fn, [{"role": "user", "content": "go"}], [],
                                 {"echo": lambda value: {"got": value}})
        self.assertEqual(result, "Done")
        self.assertEqual(seen[1][-1]["tool_call_id"], "abc123")

    def test_unknown_tool_returns_error_result_not_a_crash(self):
        calls = iter([_tool_call("nonexistent", "{}"), _final("handled")])
        result = run_agent_loop(lambda m, t: next(calls), [{"role": "user", "content": "go"}], [], {})
        self.assertEqual(result, "handled")

    def test_max_turns_exceeded_raises_rather_than_looping_forever(self):
        with self.assertRaises(RuntimeError):
            run_agent_loop(lambda m, t: _tool_call("echo", "{}"),
                            [{"role": "user", "content": "go"}], [], {"echo": lambda: {}}, max_turns=2)


class TestTools(unittest.TestCase):

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", self.repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.name", "T"], check=True, capture_output=True)
        with open(os.path.join(self.repo, "b.py"), "w") as f:
            f.write("X = 1\n")
        with open(os.path.join(self.repo, "a.py"), "w") as f:
            f.write("import b\n")
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-m", "init"], check=True, capture_output=True)
        self.db = os.path.join(self.repo, "g.db")
        run_scan(self.repo, self.db)

    def test_get_file_evidence_real_vs_missing(self):
        result = get_file_evidence(self.db, "b.py")
        self.assertEqual(result["fan_in"], 1)
        self.assertIn("error", get_file_evidence(self.db, "nonexistent.py"))

    def test_blast_radius_unscanned_db_returns_error_not_fake_zero(self):
        """Regression: an unscanned db must error, never silently return total=0."""
        bogus_db = os.path.join(self.repo, "never_scanned.db")
        self.assertIn("error", get_file_blast_radius(bogus_db, "b.py"))
        self.assertNotIn("error", get_file_blast_radius(self.db, "b.py"))

    def test_get_diff_files_reports_changed_files(self):
        with open(os.path.join(self.repo, "a.py"), "a") as f:
            f.write("# change\n")
        subprocess.run(["git", "-C", self.repo, "add", "a.py"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-m", "update"], check=True, capture_output=True)
        result = get_diff_files(self.repo, "HEAD~1", "HEAD")
        self.assertIn("a.py", {f["path"] for f in result["files"]})


class TestAgentIntegration(unittest.TestCase):

    def test_structured_json_response_produces_findings_and_checks(self):
        payload = json.dumps({"findings": ["payment.py has high fan-in"], "checks": ["run tests"]})
        result = _run_with_chat_fn(lambda m, t: _final(payload), "/repo", "/db.sqlite", [("payment.py", "M")])
        self.assertTrue(result.available)
        self.assertEqual(result.findings, ["payment.py has high fan-in"])

    def test_real_tool_error_flows_through_untouched_not_fabricated(self):
        """A genuinely unscanned db's {"error": ...} tool result must reach the
        final findings honestly, with no invented risk claim layered on top."""
        unscanned_db = os.path.join(tempfile.mkdtemp(), "never_scanned.db")
        calls = iter([
            _tool_call("get_file_evidence", json.dumps({"db_path": unscanned_db, "file_path": "ghost.py"})),
            _final(json.dumps({"findings": ["No evidence available for ghost.py."], "checks": []})),
        ])
        result = _run_with_chat_fn(lambda m, t: next(calls), "/repo", unscanned_db, [("ghost.py", "D")])
        self.assertIn("No evidence available", result.findings[0])
        self.assertNotIn("risk", result.findings[0].lower())

    def test_missing_credentials_returns_unavailable_without_network_call(self):
        for var in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
            os.environ.pop(var, None)
        result = generate_agent_findings("/repo", "/db.sqlite", [("a.py", "M")])
        self.assertFalse(result.available)
        self.assertIsNotNone(result.error)
        self.assertIn("not configured", result.error or "")

    def test_invalid_credentials_return_unavailable_not_a_crash(self):
        """Regression: credentials present but rejected by watsonx (bad project
        id, bad URL, network down) must degrade to unavailable, never raise
        past generate_agent_findings and crash the whole `analyze` command."""
        os.environ.update(WATSONX_API_KEY="key", WATSONX_URL="https://example.invalid",
                           WATSONX_PROJECT_ID="not-a-real-project")
        try:
            result = generate_agent_findings("/repo", "/db.sqlite", [("a.py", "M")])
        finally:
            for var in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
                os.environ.pop(var, None)
        self.assertFalse(result.available)
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main()
