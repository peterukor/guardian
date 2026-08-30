"""
CLI tests for Guardian.  One shared setUp builds a minimal real git repo and
runs 'guardian scan' once; each test reuses that scanned state.

Kept to ≤70 lines per the testing rule in the handoff.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest


def _run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run guardian as a module via the current Python interpreter."""
    return subprocess.run(
        [sys.executable, "-m", "src.cli"] + list(args),
        capture_output=True, text=True, cwd=cwd or os.getcwd(),
    )


class TestCLI(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        repo = self.tmp
        # Minimal git repo with two commits so HEAD~1..HEAD is valid.
        subprocess.run(["git", "init", repo], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
        # First commit: a.py imports b.py
        open(os.path.join(repo, "b.py"), "w").close()
        with open(os.path.join(repo, "a.py"), "w") as f:
            f.write("import b\n")
        subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
        # Second commit: touch a.py so HEAD~1..HEAD is non-empty.
        with open(os.path.join(repo, "a.py"), "a") as f:
            f.write("# change\n")
        subprocess.run(["git", "-C", repo, "add", "a.py"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-m", "update a"], check=True, capture_output=True)
        self.db = os.path.join(repo, "test.db")
        r = _run("scan", repo, "--db", self.db)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_scan_summary(self):
        out = _run("scan", self.tmp, "--db", self.db).stdout
        self.assertIn("Scan complete", out)
        self.assertIn("Files:", out)
        self.assertIn("Edges:", out)

    def test_analyze_diff(self):
        r = _run("analyze", self.tmp, "--diff", "HEAD~1..HEAD", "--db", self.db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GUARDIAN CHANGE PASSPORT", r.stdout)
        self.assertIn("Risk:", r.stdout)
        self.assertIn("Blast radius:", r.stdout)
        self.assertIn("[Agent unavailable", r.stdout)

    def test_analyze_json(self):
        r = _run("analyze", self.tmp, "--diff", "HEAD~1..HEAD", "--db", self.db, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("files", data)
        self.assertIn("ref_range", data)
        fp = data["files"][0]
        self.assertIn("risk_score", fp)
        self.assertIn("blast_radius_total", fp)

    def test_error_invalid_ref(self):
        r = _run("analyze", self.tmp, "--diff", "badref..HEAD", "--db", self.db)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Error", r.stderr)

    def test_deleted_file_shows_unavailable_not_stale_evidence(self):
        # Commit a deletion AFTER the scan — store still has b.py's record.
        subprocess.run(["git", "-C", self.tmp, "rm", "b.py"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-m", "delete b"], check=True, capture_output=True)
        r = _run("analyze", self.tmp, "--diff", "HEAD~1..HEAD", "--db", self.db)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Evidence unavailable", r.stdout)
        self.assertNotIn("Blast radius:", r.stdout)


if __name__ == "__main__":
    unittest.main()
