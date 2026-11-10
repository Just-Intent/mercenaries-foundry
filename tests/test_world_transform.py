import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, modbuild, ucfb, world
from mercfoundry.hashing import name_hash
from tests.test_dsk import build, chunk
from tests.test_decoders import table, wrap


def inst(model, name, props, position, rotation=(1, 0, 0, 0, 1, 0, 0, 0, 1)):
    words = [name_hash(model), name_hash(name) if name else 0]
    for k, v in props:
        words += [name_hash(k), name_hash(v)]
    # As in retail: the property pairs + 1, + 1 more for a name.
    body = chunk(b"INFO", struct.pack("<H", len(props) + (2 if name else 1)))
    body += chunk(b"XFRM", struct.pack("<12f", *rotation, *position))
    body += chunk(b"PROP", struct.pack(f"<{len(words)}I", *words))
    return chunk(b"inst", body)


def layer():
    strings = ["base", "rock", "loc_start", "Faction", "None", "lamp", "Height", "3"]
    insts = [
        inst("rock", "", [("Faction", "None")], (10, 0, 20)),
        inst("lamp", "loc_start", [("Faction", "None"), ("Height", "3")], (0, 0, 0)),
        inst("rock", "", [("Faction", "None")], (50, 0, 50)),
    ]
    total = 2 + 4 + 2
    info = struct.pack("<4I", len(insts), total, 0, 0)
    body = chunk(b"NAME", b"base\0") + chunk(b"TNAM", b"Base\0") + chunk(b"INFO", info)
    body += chunk(b"TABL", table(strings)) + b"".join(insts)
    return wrap(b"wrld", body)


class TransformTests(unittest.TestCase):
    def test_decode_and_exact_rebuild(self):
        rec = layer()
        doc = world.to_json(rec)
        insts = [c for c in doc["chunks"] if c["tag"] == "inst"]
        self.assertEqual(insts[0]["position"], [10, 0, 20])
        self.assertEqual(insts[0]["rotation"][0], [1, 0, 0])
        self.assertEqual(insts[0]["info_offset"], 1)
        self.assertEqual(insts[1]["info_offset"], 2)
        self.assertEqual(world.from_json(json.loads(json.dumps(doc))), rec)

    def test_counts_follow_edits(self):
        doc = world.to_json(layer())
        insts = [c for c in doc["chunks"] if c["tag"] == "inst"]
        insts[0]["properties"].append(["Height", "3"])
        doc["chunks"].append(json.loads(json.dumps(insts[2])))
        body = ucfb.unwrap(world.from_json(doc), b"wrld")
        info = dict(ucfb.iter_chunks(body))[b"INFO"]
        # 4 objects; INFO values 3 (was 2) + 4 + 2 + 2.
        self.assertEqual(struct.unpack("<4I", info), (4, 11, 0, 0))

    def test_turn(self):
        rows = modbuild._turn([[1, 0, 0], [0, 1, 0], [0, 0, 1]], 90)
        self.assertAlmostEqual(rows[0][2], -1.0)
        self.assertAlmostEqual(rows[2][0], 1.0)
        self.assertEqual(rows[1], [0, 1, 0])


class RuleTests(unittest.TestCase):
    def build(self, rules):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "data").mkdir()
        build(d / "data" / "assets.dsk", [(name_hash("base"), name_hash("world"), layer())])
        modbuild.new_project(d / "proj")
        (d / "proj" / "objects" / "r.json").write_text(json.dumps(rules))
        modbuild.build(d / "proj", d / "data", d / "out")
        arc = dsk.Archive(d / "out" / "assets.dsk")
        return world.to_json(arc.read(arc.records[0]))

    def insts(self, doc):
        return [c for c in doc["chunks"] if c["tag"] == "inst"]

    def test_move_by_name(self):
        doc = self.build([{"name": "loc_start", "move": [1, 5, -2]}])
        insts = self.insts(doc)
        # Moved objects go last; their old place stays, as a marker (saved
        # games know objects by their place in the layer).
        self.assertEqual((insts[1]["model"], insts[1]["template"]), ("global_location", ""))
        self.assertEqual((insts[-1]["template"], insts[-1]["position"]), ("loc_start", [1, 5, -2]))
        self.assertEqual(len(insts), 4)

    def test_near_matches_one_unnamed_object(self):
        doc = self.build([{"model": "rock", "near": [10, 0, 20], "position": [11, 0, 21]}])
        rocks = [c for c in self.insts(doc) if c["model"] == "rock"]
        self.assertEqual([r["position"] for r in rocks], [[50, 0, 50], [11, 0, 21]])
        self.assertEqual(self.insts(doc)[0]["model"], "global_location")

    def test_copy_places_new_object_and_updates_counts(self):
        doc = self.build([{"model": "rock", "near": [10, 0, 20], "copy": True, "as": "my_rock",
                           "position": [0, 0, 5], "turn": 90}])
        insts = self.insts(doc)
        self.assertEqual(len(insts), 4)
        # The game's objects keep their places; the copy goes last.
        self.assertEqual([c["model"] for c in insts[:3]], ["rock", "lamp", "rock"])
        twin = insts[3]
        self.assertEqual(twin["template"], "my_rock")
        self.assertEqual(twin["position"], [0, 0, 5])
        self.assertAlmostEqual(twin["rotation"][0][2], -1.0, places=5)
        self.assertIn("my_rock", doc["strings"])
        # Named now, so it takes the extra slot a name needs (else the game
        # reads the objects after it with shifted properties).
        self.assertEqual(twin["info_offset"], 2)
        info = [c for c in doc["chunks"] if c["tag"] == "INFO"][0]
        self.assertNotIn("hex", info)  # still the recomputed form

    def test_rule_errors(self):
        with self.assertRaises(ValueError):
            self.build([{"model": "rock", "copy": True}])          # no "as"
        with self.assertRaises(ValueError):
            self.build([{"near": [999, 0, 999], "move": [0, 1, 0]}])  # matches nothing
        with self.assertRaises(ValueError):
            self.build([{"name": "loc_start", "move": [0, 1]}])     # bad vector


if __name__ == "__main__":
    unittest.main()
