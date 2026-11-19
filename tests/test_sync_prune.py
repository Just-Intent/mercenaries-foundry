import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import sync


class PrunePackagedTests(unittest.TestCase):
    """A mod unzipped over an older playtest package: files the new package
    doesn't list go to foundry-trash before the build."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name)
        self.mod = self.game / "mods" / "Isle"
        (self.mod / "objects").mkdir(parents=True)
        (self.mod / "mod.json").write_text("{}")
        (self.mod / "objects" / "old_rules.json").write_text("[]")       # deleted since: left by the old package
        (self.mod / "objects" / "kept.json").write_text("[]")

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_a_list_nothing_moves(self):
        self.assertEqual(sync.prune_packaged(self.game, self.mod), [])
        self.assertTrue((self.mod / "objects" / "old_rules.json").exists())

    def test_files_not_listed_go_to_trash(self):
        (self.mod / sync.PACKAGE_LIST).write_text(json.dumps({"files": ["mod.json", "objects/kept.json"]}))
        moved = sync.prune_packaged(self.game, self.mod)
        self.assertEqual(moved, ["objects/old_rules.json"])
        self.assertFalse((self.mod / "objects" / "old_rules.json").exists())
        self.assertTrue((self.mod / "objects" / "kept.json").exists())
        trashed = list((self.game / "foundry-trash").rglob("old_rules.json"))
        self.assertEqual(len(trashed), 1)
        self.assertEqual(sync.prune_packaged(self.game, self.mod), [])     # nothing more the second time


if __name__ == "__main__":
    unittest.main()
