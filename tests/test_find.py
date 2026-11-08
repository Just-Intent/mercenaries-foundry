import json
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, find, modbuild, world
from mercfoundry.hashing import name_hash
from tests.test_dsk import build
from tests.test_world_transform import layer


class FindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "data").mkdir()
        build(self.dir / "data" / "assets.dsk", [(name_hash("base"), name_hash("world"), layer())])
        self.arcs = [dsk.Archive(self.dir / "data" / "assets.dsk")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_scanner(self):
        objs = list(find.objects(self.arcs))
        self.assertEqual([o["model"] for o in objs], ["rock", "lamp", "rock"])
        self.assertEqual(objs[1]["name"], "loc_start")
        self.assertEqual(objs[1]["properties"], [("Faction", "None"), ("Height", "3")])

    def test_patterns(self):
        self.assertEqual(len(find.search(self.arcs, model="ro")), 2)        # part of the name
        self.assertEqual(len(find.search(self.arcs, model="r*k")), 2)       # wildcard
        self.assertEqual(len(find.search(self.arcs, model="r*")), 2)
        self.assertEqual(len(find.search(self.arcs, model="ROCK")), 2)      # case ignored
        self.assertEqual(len(find.search(self.arcs, name="loc_*")), 1)

    def test_near_and_radius(self):
        hits = find.search(self.arcs, near=(0, 0, 0), radius=30)
        self.assertEqual([h["model"] for h in hits], ["lamp", "rock"])
        self.assertAlmostEqual(hits[1]["distance"], (10 ** 2 + 20 ** 2) ** 0.5)
        self.assertEqual(find.locate(self.arcs, "loc_start"), [("base", (0.0, 0.0, 0.0))])

    def test_properties(self):
        self.assertEqual(len(find.search(self.arcs, properties=[("Height", None)])), 1)
        self.assertEqual(len(find.search(self.arcs, properties=[("Faction", "none")])), 3)
        self.assertEqual(len(find.search(self.arcs, properties=[("Faction", "China")])), 0)

    def test_yaw_matches_turn(self):
        rows = modbuild._turn([[1, 0, 0], [0, 1, 0], [0, 0, 1]], 30)
        self.assertAlmostEqual(find.yaw([v for r in rows for v in r]), 30)

    def test_rules_from_find_match_exactly_one_object(self):
        for hit in find.search(self.arcs):
            proj = self.dir / f"proj_{hit['index']}"
            modbuild.new_project(proj)
            rule = find.rule_for(hit)
            rule["move"] = [0, 1, 0]
            (proj / "objects" / "r.json").write_text(json.dumps(rule))
            modbuild.build(proj, self.dir / "data", self.dir / f"out_{hit['index']}")
            arc = dsk.Archive(self.dir / f"out_{hit['index']}" / "assets.dsk")
            moved = [o for o in world.iter_objects(arc.read(arc.records[0])) if o["position"][1] == 1.0]
            self.assertEqual([o["index"] for o in moved], [hit["index"]])


if __name__ == "__main__":
    unittest.main()
