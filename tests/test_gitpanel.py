import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mercfoundry.editor import gitpanel

HAVE_GIT = shutil.which("git") is not None


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@unittest.skipUnless(HAVE_GIT, "git isn't installed")
class GitPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def mod(self, where=None):
        m = where or self.tmp / "mods" / "My Mod"
        m.mkdir(parents=True)
        (m / "mod.json").write_text(json.dumps({"name": "My Mod", "generated": [
            "encounters/gen_*.json", {"file": "maps/isle.json", "keys": ["hqs", "interiors"]}]}), encoding="utf-8")
        return m

    def init(self, m):
        git(m, "init", "-q")
        git(m, "config", "user.name", "Tester")
        git(m, "config", "user.email", "tester@example.invalid")

    def test_not_under_git(self):
        m = self.mod()
        self.assertEqual(gitpanel.status(m), {"git": False, "folder": "My Mod"})

    def test_a_repository_higher_up_isnt_the_mods(self):
        self.init(self.tmp)
        m = self.mod()
        self.assertIsNone(gitpanel.repo_root(m))
        self.assertFalse(gitpanel.status(m)["git"])
        with self.assertRaises(ValueError):
            gitpanel.commit(m, "x", ["mod.json"])

    def test_status_marks_generated_files_and_commits_only_the_picked(self):
        m = self.mod()
        self.init(m)
        (m / "encounters").mkdir()
        (m / "encounters" / "gen_a.json").write_text("[]", encoding="utf-8")
        (m / "encounters" / "mine.json").write_text("[]", encoding="utf-8")
        (m / "maps").mkdir()
        (m / "maps" / "isle.json").write_text("{}", encoding="utf-8")
        st = gitpanel.status(m)
        self.assertTrue(st["git"])
        files = {f["path"]: f for f in st["files"]}
        self.assertEqual(set(files), {"mod.json", "encounters/gen_a.json", "encounters/mine.json", "maps/isle.json"})
        self.assertIs(files["encounters/gen_a.json"]["generated"], True)
        self.assertIsNone(files["encounters/mine.json"]["generated"])
        self.assertEqual(files["maps/isle.json"]["generated"], ["hqs", "interiors"])
        self.assertEqual(files["mod.json"]["kind"], "new")
        self.assertEqual(st["identity"], "Tester <tester@example.invalid>")
        self.assertEqual(st["remotes"], [])
        with self.assertRaises(ValueError):
            gitpanel.commit(m, "  ", ["mod.json"])            # no message
        with self.assertRaises(ValueError):
            gitpanel.commit(m, "x", ["../elsewhere"])
        done = gitpanel.commit(m, "First", ["mod.json", "encounters/mine.json"])
        self.assertIn("First", done["committed"])
        left = {f["path"] for f in done["files"]}
        self.assertEqual(left, {"encounters/gen_a.json", "maps/isle.json"})
        log = subprocess.run(["git", "-C", str(m), "log", "--format=%an|%B"], capture_output=True, text=True).stdout
        self.assertEqual(log.strip(), "Tester|First")
        # a deletion commits too
        (m / "encounters" / "mine.json").unlink()
        st = gitpanel.status(m)
        self.assertIn({"path": "encounters/mine.json", "kind": "deleted", "generated": None}, st["files"])
        gitpanel.commit(m, "Gone", ["encounters/mine.json"])
        self.assertNotIn("encounters/mine.json", {f["path"] for f in gitpanel.status(m)["files"]})

    def test_a_git_file_naming_a_git_dir_elsewhere(self):
        m = self.mod()
        gd = self.tmp / "store.git"
        git(self.tmp, "init", "-q", "--separate-git-dir", str(gd), str(m))
        git(m, "config", "user.name", "Tester")
        git(m, "config", "user.email", "tester@example.invalid")
        self.assertTrue((m / ".git").is_file())
        self.assertEqual(gitpanel.repo_root(m), m.resolve())
        self.assertIn("mod.json", {f["path"] for f in gitpanel.status(m)["files"]})

    def test_push_needs_a_remote(self):
        m = self.mod()
        self.init(m)
        gitpanel.commit(m, "First", ["mod.json"])
        with self.assertRaises(ValueError) as e:
            gitpanel.push(m)
        self.assertIn("no remote", str(e.exception))
        bare = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(bare))
        git(m, "remote", "add", "origin", str(bare))
        gitpanel.push(m)
        st = gitpanel.status(m)
        self.assertTrue(st["upstream"])
        self.assertEqual(st["ahead"], 0)

    def test_build_needs_a_game_folder(self):
        with self.assertRaises(ValueError):
            gitpanel.Builds().start(None)


if __name__ == "__main__":
    unittest.main()
