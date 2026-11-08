import struct
import unittest

from mercfoundry import aidata, encounters, ucfb, world

SOURCE = {"tag": "inst", "model": "nk_hum_soldier_easy", "template": "RoadblockRunner", "info_offset": 2,
          "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "position": [0, 0, 0], "chunks": ["INFO", "XFRM", "PROP"],
          "properties": [["objectType", "human"], ["faction", "nk"], ["encounter", "none"], ["squad", "none"],
                         ["path", "p_somewhere"]]}

DOC = {"map": "swn",
       "encounters": [{"name": "Ambush", "points": {"A": [[1, 2, 3], [4, 5, 6]], "B": [[7, 8, 9]]},
                       "squads": [{"name": "Squad1", "points": ["A", "B"],
                                   "members": [{"from": "RoadblockRunner", "name": "Nk01", "position": [10, 0, 20], "yaw": 90},
                                               {"from": "RoadblockRunner", "name": "Nk02", "position": [12, 0, 20],
                                                "set": {"hitPoints": "999"}}]}]}],
       "objects": [{"from": "RoadblockRunner", "name": "Loner", "position": [0, 0, 0]}]}


class EncounterTests(unittest.TestCase):
    def build(self, doc=DOC):
        return encounters.build("my_layer", doc, lambda name: SOURCE)

    def test_world(self):
        rec = self.build()[encounters.WORLD]
        doc = world.to_json(rec)
        self.assertEqual(doc["name"], "my_layer")
        insts = [c for c in doc["chunks"] if c.get("tag") == "inst"]
        self.assertEqual([i["template"] for i in insts], ["Nk01", "Nk02", "Loner"])
        props = {k: v for k, v in insts[0]["properties"]}
        self.assertEqual((props["encounter"], props["squad"], props["path"]), ("Ambush", "Squad1", "none"))
        self.assertEqual(dict(insts[1]["properties"])["hitPoints"], "999")
        self.assertEqual(dict(insts[2]["properties"])["encounter"], "none")       # plain objects keep theirs
        self.assertAlmostEqual(insts[0]["rotation"][0][2], -1.0, places=5)        # yaw 90
        self.assertEqual(insts[0]["position"], [10.0, 0.0, 20.0])
        tnam = [c for c in doc["chunks"] if c.get("tag") == "TNAM"][0]
        self.assertEqual(bytes.fromhex(tnam["hex"]), b"SWN\0")
        self.assertEqual(world.from_json(doc), rec)                              # round-trips
        self.assertEqual(SOURCE["template"], "RoadblockRunner")                   # source untouched

    def test_encounter(self):
        rec = self.build()[encounters.ENCOUNTER]
        doc = aidata.to_json(rec, "encounter")
        self.assertEqual(aidata.from_json(doc), rec)
        enc = doc["tree"][0]["children"][0]
        kids = {c["tag"]: c for c in enc["children"]}
        self.assertEqual(kids["NAME"]["text"], "Ambush")
        self.assertEqual(struct.unpack("<4H", bytes.fromhex(kids["INFO"]["hex"]))[2:], (2, 1))
        psets = kids["PSTS"]["children"]
        self.assertEqual([p["children"][0]["text"] for p in psets], ["A", "B"])
        self.assertEqual(struct.unpack_from("<H", bytes.fromhex(psets[0]["children"][1]["hex"]), 4)[0], 2)
        sq = {c["tag"]: c for c in kids["SQDS"]["children"][0]["children"]}
        self.assertEqual(struct.unpack("<HHI", bytes.fromhex(sq["INFO"]["hex"])), (2, 0, 2))
        self.assertEqual(sq["FPNT"]["sets"], ["A", "B"])

    def test_list(self):
        text = self.build()[encounters.LIST].decode()
        self.assertEqual(text, "\n[encounter]\n\nload my_layer\n\n[world]\n\nload my_layer\n")

    def test_errors(self):
        bad = {"encounters": [{"name": "E", "points": {}, "squads": [{"name": "S", "points": ["Z"], "members": []}]}]}
        with self.assertRaises(ValueError):
            self.build(bad)
        dup = {"objects": [{"from": "x", "name": "Same", "position": [0, 0, 0]},
                           {"from": "x", "name": "Same", "position": [1, 0, 0]}]}
        with self.assertRaises(ValueError):
            self.build(dup)


if __name__ == "__main__":
    unittest.main()
