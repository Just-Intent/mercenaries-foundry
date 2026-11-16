import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk
from mercfoundry.editor.scene import Scene
from mercfoundry.hashing import name_hash
from tests.test_aidata import boundary_record
from tests.test_dsk import build as build_dsk


def scene_for(root):
    """A scene on one archive holding map "base"'s own zones record (a killbox)."""
    (root / "data").mkdir()
    build_dsk(root / "data" / "assets.dsk", [(name_hash("base"), name_hash("lineregion"), boundary_record())])
    sc = Scene.__new__(Scene)
    sc.archives = [dsk.Archive(root / "data" / "assets.dsk")]
    sc.mod = root / "mod"
    sc.mod.mkdir()
    sc.context = [sc.mod]
    sc._names = {name_hash("base"): "base"}
    return sc


SEA = {"points": [[0, 1, 0], [50, 1, 0], [50, 1, 50]], "properties": {"type": "water", "height": "1.5"}}


class MapWaterTests(unittest.TestCase):
    """Water goes in the map's own zones record (the game makes water only there)."""

    def test_game_map_water_saves_in_its_own_record(self):
        with tempfile.TemporaryDirectory() as d:
            sc = scene_for(Path(d))
            own = sc.layout("base")["own"]
            self.assertEqual(own["file"], "layout/base.lineregion.changes.json")
            self.assertTrue(own["editable"])
            self.assertEqual(own["water_limit"], 16)
            done = sc.save_layout("base", [], {}, {"sea": json.loads(json.dumps(SEA))})
            self.assertEqual(done["files"], ["layout/base.lineregion.changes.json"])
            items = {it["name"]: it for it in sc.layout("base")["items"]}
            self.assertEqual(items["killbox"]["source"], "game")
            self.assertEqual(items["sea"]["source"], "map")
            self.assertEqual(items["sea"]["properties"], {"type": "water", "height": "1.5"})
            # a game zone moved too: both kept in the one changes file
            sc.save_layout("base", [{"layer": "base", "kind": "boundary", "name": "killbox",
                                     "points": [[0, 0, 0], [6, 0, 0], [0, 0, 6]]}], {}, None)
            changed = json.loads((sc.mod / "layout" / "base.lineregion.changes.json").read_text(encoding="utf-8"))["changed"]
            self.assertEqual(sorted(c["children"][0]["text"] for c in changed), ["killbox", "sea"])
            # removing the added zone keeps the moved game one
            sc.save_layout("base", [], {}, {})
            changed = json.loads((sc.mod / "layout" / "base.lineregion.changes.json").read_text(encoding="utf-8"))["changed"]
            self.assertEqual([c["children"][0]["text"] for c in changed], ["killbox"])
            # a game zone's name is taken; the game holds 16 water zones a map
            with self.assertRaises(ValueError):
                sc.save_own_zones("base", {"KILLBOX": json.loads(json.dumps(SEA))})
            with self.assertRaises(ValueError):
                sc.save_own_zones("base", {f"sea{i}": json.loads(json.dumps(SEA)) for i in range(17)})
            with self.assertRaises(ValueError):
                sc.save_own_zones("base", {"thin": {"points": [[0, 0, 0], [1, 0, 0]], "properties": {}}})

    def test_new_map_water_goes_in_its_boundaries_file(self):
        with tempfile.TemporaryDirectory() as d:
            sc = scene_for(Path(d))
            (sc.mod / "maps").mkdir()
            (sc.mod / "maps" / "isle.json").write_text('{"from": "blank"}', encoding="utf-8")
            self.assertEqual(sc.layout("isle")["own"]["file"], "maps/isle/boundaries.json")
            sc.save_own_zones("isle", {"sea": json.loads(json.dumps(SEA))})
            saved = json.loads((sc.mod / "maps" / "isle" / "boundaries.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["boundaries"]["sea"]["properties"]["height"], "1.5")
            sea = next(it for it in sc.layout("isle")["items"] if it["name"] == "sea")
            self.assertEqual((sea["source"], sea["editable"]), ("map", True))
            sc.save_own_zones("isle", {})
            self.assertFalse((sc.mod / "maps" / "isle" / "boundaries.json").exists())
            # a map another mod makes: shown, not saved from here
            other = Path(d) / "other"
            (other / "maps" / "far").mkdir(parents=True)
            (other / "maps" / "far.json").write_text('{"from": "blank"}', encoding="utf-8")
            (other / "maps" / "far" / "boundaries.json").write_text(json.dumps({"boundaries": {"lake": SEA}}), encoding="utf-8")
            sc.context = [sc.mod, other]
            lay = sc.layout("far")
            self.assertFalse(lay["own"]["editable"])
            self.assertFalse(next(it for it in lay["items"] if it["name"] == "lake")["editable"])
            with self.assertRaises(ValueError):
                sc.save_own_zones("far", {})


if __name__ == "__main__":
    unittest.main()
