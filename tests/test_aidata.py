import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import aidata, codecs, dsk, modbuild, ucfb
from mercfoundry.hashing import name_hash
from tests.test_dsk import build, chunk


def pts(*p):
    return b"".join(struct.pack("<3f", *x) for x in p)


def path_record():
    props = b"width\0" + struct.pack("<f", 2.5) + b"num_pause_times\0" + struct.pack("<I", 0)
    body = (chunk(b"NAME", b"road1\0") + chunk(b"INFO", struct.pack("<3H", 2, 7, 2)) + chunk(b"PROP", props)
            + chunk(b"PNTS", pts((0, 0, 0), (10, 0, 0))) + chunk(b"ORNT", struct.pack("<8f", 0, 0, 0, 1, 0, 0, 0, 1))
            + chunk(b"JNCT", b"\0\0"))
    return chunk(b"ucfb", chunk(b"path", body))


def boundary_record():
    a = (chunk(b"NAME", b"killbox\0") + chunk(b"INFO", struct.pack("<3H", 0, 2, 3))
         + chunk(b"PROP", b"type\0killbox\0TestInside\0false\0") + chunk(b"PNTS", pts((0, 0, 0), (5, 0, 0), (0, 0, 5))))
    b = (chunk(b"NAME", b"empty\0") + chunk(b"INFO", struct.pack("<3H", 0, 0, 1))
         + chunk(b"PROP", b"") + chunk(b"PNTS", pts((1, 1, 1))))
    return chunk(b"ucfb", chunk(b"lrg_", a) + chunk(b"lrg_", b))


def region_record():
    xfrm = struct.pack("<12f", 1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 6, 7)
    regn = chunk(b"INFO", struct.pack("<H", 1)) + chunk(b"XFRM", xfrm) + chunk(b"SIZE", struct.pack("<3f", 1, 2, 3)) \
        + chunk(b"PROP", b"type\0weather\0")
    rgns = chunk(b"INFO", struct.pack("<2I", 1, 1)) + chunk(b"regn", regn)
    return chunk(b"ucfb", chunk(b"rgns", rgns))


def encounter_record():
    pset = chunk(b"NAME", b"A\0") + chunk(b"INFO", struct.pack("<IH", 1, 2)) + chunk(b"PNTS", pts((1, 0, 1), (2, 0, 2)))
    squad = chunk(b"NAME", b"nk_squad\0") + chunk(b"INFO", struct.pack("<2I", 7, 18)) + chunk(b"FPNT", b"A\0")
    enc = (chunk(b"NAME", b"camp\0") + chunk(b"INFO", struct.pack("<4H", 0, 0, 1, 1))
           + chunk(b"PSTS", chunk(b"PSET", pset)) + chunk(b"SQDS", chunk(b"SQD_", squad)))
    return chunk(b"ucfb", chunk(b"enc_", enc))


SAMPLES = {"path": path_record, "lineregion": boundary_record, "region": region_record, "encounter": encounter_record}


def node(doc, tag, name=None):
    for t, n, item in aidata.items(doc):
        if t == tag and (name is None or n == name):
            return item
    raise KeyError((tag, name))


def child(item, tag):
    return [c for c in item["children"] if c["tag"] == tag][0]


class AiDataTests(unittest.TestCase):
    def test_exact_round_trip(self):
        for kind, make in SAMPLES.items():
            rec = make()
            doc = json.loads(json.dumps(aidata.to_json(rec, kind)))
            self.assertEqual(aidata.from_json(doc), rec, kind)

    def test_readable_values(self):
        doc = aidata.to_json(path_record(), "path")
        p = node(doc, "path", "road1")
        self.assertEqual(child(p, "PROP")["values"], [["width", 2.5], ["num_pause_times", 0]])
        self.assertEqual(child(p, "PNTS")["points"][1], [10, 0, 0])
        self.assertEqual(child(p, "JNCT")["links"], [])         # no links (u16 count 0)
        doc = aidata.to_json(boundary_record(), "lineregion")
        self.assertEqual(child(node(doc, "lrg_", "killbox"), "PROP")["pairs"], [["type", "killbox"], ["TestInside", "false"]])
        self.assertEqual(child(node(doc, "lrg_", "empty"), "PROP")["pairs"], [])
        doc = aidata.to_json(region_record(), "region")
        self.assertEqual(child(node(doc, "regn"), "XFRM")["position"], [5, 6, 7])
        doc = aidata.to_json(encounter_record(), "encounter")
        self.assertEqual(child(node(doc, "SQD_", "nk_squad"), "FPNT")["sets"], ["A"])

    def test_counts_follow_edits(self):
        doc = aidata.to_json(boundary_record(), "lineregion")
        kb = node(doc, "lrg_", "killbox")
        child(kb, "PNTS")["points"].append([9, 9, 9])
        child(kb, "PROP")["pairs"].append(["Extra", "1"])
        out = aidata.to_json(aidata.from_json(doc), "lineregion")
        info = bytes.fromhex(child(node(out, "lrg_", "killbox"), "INFO")["hex"])
        self.assertEqual(struct.unpack("<3H", info), (0, 3, 4))

        doc = aidata.to_json(encounter_record(), "encounter")
        pset = node(doc, "PSET", "A")
        child(pset, "PNTS")["points"].append([3, 0, 3])
        out = aidata.to_json(aidata.from_json(doc), "encounter")
        info = bytes.fromhex(child(node(out, "PSET", "A"), "INFO")["hex"])
        self.assertEqual(struct.unpack("<IH", info), (1, 3))

        doc = aidata.to_json(path_record(), "path")
        p = node(doc, "path")
        child(p, "PNTS")["points"].append([20, 0, 0])
        child(p, "ORNT")["rotations"].append([0, 0, 0, 1])
        out = aidata.to_json(aidata.from_json(doc), "path")
        info = bytes.fromhex(child(node(out, "path"), "INFO")["hex"])
        self.assertEqual(struct.unpack("<3H", info), (3, 7, 2))  # middle field kept

    def test_path_points_and_rotations_must_match(self):
        doc = aidata.to_json(path_record(), "path")
        child(node(doc, "path"), "PNTS")["points"].append([20, 0, 0])
        with self.assertRaises(ValueError):
            aidata.from_json(doc)

    def test_codec_suffixes(self):
        self.assertEqual(codecs.decode(name_hash("lineregion"), boundary_record())[0], ".lineregion.json")
        self.assertEqual(codecs.decode(name_hash("list"), b"[animation]\nload x\n"), (".list.txt", b"[animation]\nload x\n"))

    def test_mod_layout_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            build(d / "data" / "assets.dsk", [
                (name_hash("base"), name_hash("lineregion"), boundary_record()),
                (name_hash("base"), name_hash("list"), b"[animation]\n"),
            ])
            modbuild.new_project(d / "proj")
            doc = aidata.to_json(boundary_record(), "lineregion")
            child(node(doc, "lrg_", "killbox"), "PNTS")["points"][0] = [-5, 0, -5]
            (d / "proj" / "layout" / "base.lineregion.json").write_text(json.dumps(doc))
            (d / "proj" / "layout" / "base.list.txt").write_bytes(b"[animation]\nload more\n")
            modbuild.build(d / "proj", d / "data", d / "out")
            arc = dsk.Archive(d / "out" / "assets.dsk")
            recs = {r.kind: arc.read(r) for r in arc.records}
            out = aidata.to_json(recs[name_hash("lineregion")], "lineregion")
            self.assertEqual(child(node(out, "lrg_", "killbox"), "PNTS")["points"][0], [-5, 0, -5])
            self.assertEqual(recs[name_hash("list")], b"[animation]\nload more\n")
            (d / "proj" / "layout" / "nolayer.path.json").write_text(json.dumps(aidata.to_json(path_record(), "path")))
            with self.assertRaises(ValueError):
                modbuild.build(d / "proj", d / "data", d / "out2")


if __name__ == "__main__":
    unittest.main()
