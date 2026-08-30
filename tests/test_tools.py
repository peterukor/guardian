"""
Tests for src/agent/tools.py. One real scanned repo, reused across tests.
"""

import os
import subprocess
import tempfile
import unittest

from src.evidence_store import EvidenceStore
from src.scanner import run_scan
from src.agent.tools import get_file_evidence, get_file_blast_radius, get_diff_files


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

    def test_get_file_evidence_returns_real_data(self):
        result = get_file_evidence(self.db, "b.py")
        self.assertNotIn("error", result)
        self.assertEqual(result["fan_in"], 1)
        self.assertIn("risk_score", result)
        self.assertIn("risk_level", result)

    def test_get_file_evidence_missing_file_returns_error(self):
        result = get_file_evidence(self.db, "nonexistent.py")
        self.assertIn("error", result)

    def test_get_file_blast_radius_unscanned_returns_error_not_zero(self):
        """Regression: an unscanned db must return an error, never a fake zero."""
        bogus_db = os.path.join(self.repo, "never_scanned.db")
        result = get_file_blast_radius(bogus_db, "b.py")
        self.assertIn("error", result)

    def test_get_file_blast_radius_real_file(self):
        result = get_file_blast_radius(self.db, "b.py")
        self.assertNotIn("error", result)
        self.assertEqual(result["total"], 1)
        self.assertIn("a.py", result["direct_dependents"])

    def test_get_diff_files_returns_changed_files(self):
        with open(os.path.join(self.repo, "a.py"), "a") as f:
            f.write("# change\n")
        subprocess.run(["git", "-C", self.repo, "add", "a.py"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-m", "update"], check=True, capture_output=True)

        result = get_diff_files(self.repo, "HEAD~1", "HEAD")
        self.assertNotIn("error", result)
        paths = {f["path"] for f in result["files"]}
        self.assertIn("a.py", paths)


if __name__ == "__main__":
    unittest.main()
