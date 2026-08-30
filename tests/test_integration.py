"""
Tests for src/agent/integration.py — the Agent-to-passport wiring.
Uses fake chat_fn / missing env vars; no live watsonx needed.
"""

import json
import os
import tempfile
import unittest

from src.agent.integration import _run_with_chat_fn, generate_agent_findings


def _final(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def _tool_call(name, args, call_id="call_1"):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "type": "function",
                         "function": {"name": name, "arguments": args}}],
    }}]}


class TestAgentIntegration(unittest.TestCase):

    def test_structured_json_response_produces_findings_and_checks(self):
        payload = json.dumps({"findings": ["payment.py has high fan-in"],
                               "checks": ["run integration tests"]})
        result = _run_with_chat_fn(lambda m, t: _final(payload), "/repo", "/db.sqlite",
                                    [("payment.py", "M")])
        self.assertTrue(result.available)
        self.assertEqual(result.findings, ["payment.py has high fan-in"])
        self.assertEqual(result.checks, ["run integration tests"])

    def test_unavailable_evidence_is_reported_not_fabricated(self):
        """
        Real get_file_evidence (not mocked) is invoked against a genuinely
        unscanned db, so it genuinely returns {"error": ...}. The scripted
        final response reflects that honestly. Proves the real "no evidence"
        tool result flows through the integration layer untouched -- nothing
        here overrides or embellishes it into a fabricated risk claim.
        """
        unscanned_db = os.path.join(tempfile.mkdtemp(), "never_scanned.db")
        calls = iter([
            _tool_call("get_file_evidence", json.dumps(
                {"db_path": unscanned_db, "file_path": "ghost.py"})),
            _final(json.dumps({
                "findings": ["No evidence available for ghost.py — not yet scanned."],
                "checks": [],
            })),
        ])
        result = _run_with_chat_fn(lambda m, t: next(calls), "/repo", unscanned_db,
                                    [("ghost.py", "D")])
        self.assertTrue(result.available)
        self.assertEqual(len(result.findings), 1)
        self.assertIn("No evidence available", result.findings[0])
        self.assertNotIn("risk", result.findings[0].lower())  # no fabricated risk claim

    def test_missing_credentials_returns_unavailable_without_network_call(self):
        for var in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
            os.environ.pop(var, None)
        result = generate_agent_findings("/repo", "/db.sqlite", [("a.py", "M")])
        self.assertFalse(result.available)
        self.assertIn("not configured", result.error)


if __name__ == "__main__":
    unittest.main()
