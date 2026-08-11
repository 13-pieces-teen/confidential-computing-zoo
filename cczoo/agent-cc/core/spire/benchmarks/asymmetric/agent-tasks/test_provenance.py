import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import provenance


class ProvenanceTests(unittest.TestCase):
    def test_clean_commit_is_accepted_and_identified(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-b", "evaluation"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "E8 Test"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)

            result = provenance.collect(repo)

            self.assertTrue(result["clean"])
            self.assertEqual(result["branch"], "evaluation")
            self.assertRegex(result["commit"], r"^[0-9a-f]{40}$")

            (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, provenance.__file__, "--repo", str(repo), "--require-clean"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(rejected.stdout, "")
            self.assertIn("clean committed worktree", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
