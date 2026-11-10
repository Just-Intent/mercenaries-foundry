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

    def test_game_squads(self):
        # A squad for a game encounter: members in this layer, the record
        # added to the game's encounter (here one built from DOC).
        doc = {"map": "swn", "game_squads": [{"layer": "game_layer", "encounter": "Ambush", "name": "Extra",
                                             "points": ["B"],
                                             "members": [{"from": "RoadblockRunner", "name": "Nk09",
                                                          "position": [1, 0, 1]}]}]}
        insts = [c for c in world.to_json(self.build(doc)[encounters.WORLD])["chunks"] if c.get("tag") == "inst"]
        props = dict(insts[0]["properties"])
        self.assertEqual((insts[0]["template"], props["encounter"], props["squad"]), ("Nk09", "Ambush", "Extra"))
        self.assertNotIn(encounters.ENCOUNTER, self.build(doc))       # no encounter of its own
        game = aidata.to_json(self.build()[encounters.ENCOUNTER], "encounter")
        encounters.add_game_squads(game, doc["game_squads"])
        game = aidata.to_json(aidata.from_json(game), "encounter")      # rebuilt
        enc = {c["tag"]: c for c in game["tree"][0]["children"][0]["children"]}
        self.assertEqual(struct.unpack("<4H", bytes.fromhex(enc["INFO"]["hex"]))[2:], (2, 2))
        sq = {c["tag"]: c for c in enc["SQDS"]["children"][1]["children"]}
        self.assertEqual((sq["NAME"]["text"], sq["FPNT"]["sets"]), ("Extra", ["B"]))
        self.assertEqual(struct.unpack("<HHI", bytes.fromhex(sq["INFO"]["hex"])), (1, 0, 1))
        for bad in ({"points": ["Z"]}, {"encounter": "Nope"}, {"name": "Squad1"}):
            with self.assertRaises(ValueError):
                encounters.add_game_squads(game, [{**doc["game_squads"][0], **bad}])

    def test_list(self):
        text = self.build()[encounters.LIST].decode()
        self.assertEqual(text, "\n[encounter]\n\nload my_layer\n\n[world]\n\nload my_layer\n")

    def test_paths(self):
        doc = {"paths": {"loop": {"points": [[0, 0, 0], [10, 0, 0], [10, 0, 10]], "pauses": {"1": 2.5},
                                  "links": [{"path": "loop"}]}}}
        recs = self.build(doc)
        self.assertIn("[path]\n\nload my_layer\n", recs[encounters.LIST].decode())
        pdoc = aidata.to_json(recs[encounters.PATH], "path")
        self.assertEqual(aidata.from_json(pdoc), recs[encounters.PATH])
        kids = {c["tag"]: c for c in pdoc["tree"][0]["children"][0]["children"]}
        self.assertEqual([c["tag"] for c in pdoc["tree"][0]["children"][0]["children"]],
                         ["NAME", "INFO", "PROP", "PSTM", "PNTS", "ORNT", "JNCT"])
        self.assertEqual(kids["PSTM"]["at_points"], [[1, 2.5]])
        self.assertEqual(dict(kids["PROP"]["values"])["num_pause_times"], 1)
        self.assertEqual(kids["JNCT"]["links"], [{"path": "loop", "their_point": 0, "our_point": 2}])
        self.assertEqual(struct.unpack("<3H", bytes.fromhex(kids["INFO"]["hex"])), (3, 0, 3))
        with self.assertRaises(ValueError):
            self.build({"paths": {"bad": {"points": [[0, 0, 0], [1, 0, 0]], "pauses": {"5": 1}}}})

    def test_boundaries(self):
        doc = {"boundaries": {"zone": {"points": [[0, 0, 0], [10, 0, 0], [10, 0, 10], [0, 0, 10]],
                                       "properties": {"type": "killbox", "TestInside": "false"}},
                              "trigger": {"points": [[0, 0, 0], [5, 0, 0], [5, 0, 5]]}}}
        recs = self.build(doc)
        self.assertIn("[lineregion]\n\nload my_layer\n", recs[encounters.LIST].decode())
        bdoc = aidata.to_json(recs[encounters.LINEREGION], "lineregion")
        self.assertEqual(aidata.from_json(bdoc), recs[encounters.LINEREGION])
        zone, trigger = bdoc["tree"][0]["children"]
        kids = {c["tag"]: c for c in zone["children"]}
        self.assertEqual([c["tag"] for c in zone["children"]], ["NAME", "INFO", "PROP", "PNTS"])
        self.assertEqual(kids["PROP"]["pairs"], [["type", "killbox"], ["TestInside", "false"]])
        self.assertEqual(struct.unpack("<3H", bytes.fromhex(kids["INFO"]["hex"])), (0, 2, 4))
        tkids = {c["tag"]: c for c in trigger["children"]}
        self.assertEqual(struct.unpack("<3H", bytes.fromhex(tkids["INFO"]["hex"])), (0, 0, 3))
        with self.assertRaises(ValueError):
            self.build({"boundaries": {"line": {"points": [[0, 0, 0], [1, 0, 0]]}}})

    def test_regions(self):
        doc = {"regions": {"site": {"type": "box", "position": [1, 2, 3], "size": [20, 10, 20], "yaw": 90},
                           "ball": {"type": "sphere", "position": [0, 0, 0], "size": 5,
                                    "properties": {"layer": "3"}}}}
        recs = self.build(doc)
        self.assertIn("[region]\n\nload my_layer\n", recs[encounters.LIST].decode())
        rdoc = aidata.to_json(recs[encounters.REGION], "region")
        self.assertEqual(aidata.from_json(rdoc), recs[encounters.REGION])
        rgns = rdoc["tree"][0]["children"][0]
        self.assertEqual(struct.unpack("<2I", bytes.fromhex(rgns["children"][0]["hex"])), (2, 5))
        site, ball = rgns["children"][1:]
        kids = {c["tag"]: c for c in site["children"]}
        self.assertEqual([c["tag"] for c in site["children"]], ["INFO", "XFRM", "SIZE", "PROP"])
        self.assertEqual(kids["PROP"]["pairs"], [["type", "box"], ["name", "site"]])
        self.assertEqual(kids["XFRM"]["position"], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(kids["XFRM"]["rotation"][0][2], -1.0)
        bkids = {c["tag"]: c for c in ball["children"]}
        self.assertEqual(bkids["SIZE"]["size"], [5.0, 5.0, 5.0])
        self.assertEqual(bkids["PROP"]["pairs"], [["type", "sphere"], ["name", "ball"], ["layer", "3"]])
        with self.assertRaises(ValueError):
            self.build({"regions": {"cone": {"type": "cone", "position": [0, 0, 0]}}})

    def test_traffic_zones(self):
        ini = "[TrafficZone]\r\nName\t\t\tdmz-peds\r\nDensity\t\t\t50\r\n"
        out = encounters.add_traffic_zones(ini, {"foundry-m10": {"density": 100, "civ_density": 20},
                                                 "foundry-air": {}})
        self.assertTrue(out.startswith(ini))
        self.assertEqual(encounters.traffic_zone_names(out), ["dmz-peds", "foundry-m10", "foundry-air"])
        self.assertIn("[TrafficZone]\r\nName\t\t\tfoundry-m10\r\nDensity\t\t\t100\r\nCivDensity\t\t20\r\n", out)
        with self.assertRaises(ValueError):
            encounters.add_traffic_zones(ini, {"DMZ-PEDS": {}})
        many = "".join(f"[TrafficZone]\r\nName\t\t\tz{i}\r\n" for i in range(150))
        with self.assertRaises(ValueError):
            encounters.add_traffic_zones(many, {"one-more": {}})
        doc = {"paths": {"p_foundry-m10_01a": {}, "t_foundry-m10_02": {}, "p_foundry-m100_01": {}, "other": {}}}
        self.assertEqual(encounters.zone_paths(doc, "foundry-m10"), ["p_foundry-m10_01a", "t_foundry-m10_02"])

    def test_path_events(self):
        doc = {"paths": {"walk": {"points": [[0, 0, 0], [5, 0, 0], [10, 0, 0]],
                                  "events": {"2": ["all_allies_fb_fidget03"],
                                             "0": [{"anim": "all_allies_fb_fidget01", "value": 50},
                                                   "all_allies_fb_idle"]}}}}
        recs = self.build(doc)
        pdoc = aidata.to_json(recs[encounters.PATH], "path")
        self.assertEqual(aidata.from_json(pdoc), recs[encounters.PATH])
        kids = pdoc["tree"][0]["children"][0]["children"]
        self.assertEqual([c["tag"] for c in kids], ["NAME", "INFO", "PROP", "EVNT", "PNTS", "ORNT", "JNCT"])
        ev = kids[3]["nodes"]
        self.assertEqual([n["point"] for n in ev], [0, 2])
        self.assertEqual(ev[0]["events"][0], {"anim": "all_allies_fb_fidget01", "value": 50.0, "flags": 0})
        self.assertEqual(ev[0]["events"][1]["value"], 100.0)     # a bare clip name always plays
        self.assertEqual(dict(kids[2]["values"])["num_nodes_with_events"], 2.0)
        with self.assertRaises(ValueError):
            self.build({"paths": {"bad": {"points": [[0, 0, 0], [1, 0, 0]], "events": {"5": ["x"]}}}})
        with self.assertRaises(ValueError):
            self.build({"paths": {"both": {"points": [[0, 0, 0], [1, 0, 0]], "events": {"0": ["x"]},
                                           "pauses": {"1": 2}}}})

    def test_junction_codec(self):
        body = struct.pack("<H", 2) + b"t_a\0" + struct.pack("<HH", 0, 11) + b"t_b\0" + struct.pack("<HH", 2, 1)
        node = aidata._decode_leaf(b"JNCT", body, b"path")
        self.assertEqual(node["links"], [{"path": "t_a", "their_point": 0, "our_point": 11},
                                         {"path": "t_b", "their_point": 2, "our_point": 1}])
        self.assertEqual(aidata._encode_leaf(node), body)
        rotn = b"1\0" + struct.pack("<4f", 0, 1, 0, 0) + b"10\0" + struct.pack("<4f", 0, 0, 0, 1)
        node = aidata._decode_leaf(b"ROTN", rotn, b"path")
        self.assertEqual(node["facings"], [[1, 0.0, 1.0, 0.0, 0.0], [10, 0.0, 0.0, 0.0, 1.0]])
        self.assertEqual(aidata._encode_leaf(node), rotn)

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
