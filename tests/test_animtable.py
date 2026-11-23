import json
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import aidata, animation, animtable, dsk, gltf, modbuild, ucfb
from mercfoundry.hashing import name_hash
from tests.test_animation import _model, anim_record
from tests.test_dsk import build as build_dsk


def chunk(tag, data):
    return ucfb.pack_chunk(tag, data)


def entry(handle, anim=None, varg=None, prio=40, flags=5):
    body = chunk(b"HNDL", handle.encode() + b"\0")
    if anim:
        body += chunk(b"ANAM", anim.encode() + b"\0")
    if varg:
        body += chunk(b"VARG", struct.pack("<2I", *varg))
    return chunk(b"entr", body + chunk(b"PRIO", struct.pack("<I", prio)) + chunk(b"FLGS", struct.pack("<I", flags)))


def table(name="guy_table"):
    body = chunk(b"NAME", name.encode() + b"\0") + chunk(b"DFLT", b"common\0")
    body += chunk(b"SIZE", struct.pack("<2I", 2, 1))
    body += chunk(b"vanm", chunk(b"ANAM", b"fidget_a\0")) + chunk(b"vanm", chunk(b"ANAM", b"fidget_b\0"))
    aset = chunk(b"NAME", b"default\0") + chunk(b"SIZE", struct.pack("<I", 2))
    aset += entry("lb_run", "test_anim") + entry("fb_fidget", varg=(2, 0), prio=10, flags=0x20)
    body += chunk(b"aset", aset)
    return chunk(b"ucfb", chunk(b"atbl", body))


class AnimTableTests(unittest.TestCase):
    def test_round_trip(self):
        rec = table()
        doc = animtable.to_json(rec)
        self.assertEqual(doc["fallback"], "common")
        self.assertEqual(doc["sets"][0]["entries"][1]["variants"], {"start": 0, "count": 2})
        self.assertEqual(animtable.from_json(json.loads(json.dumps(doc))), rec)
        self.assertEqual(animtable.clips(doc), {"fidget_a", "fidget_b", "test_anim"})
        # flags read out in words (not read back)
        self.assertEqual(doc["sets"][0]["entries"][0]["meaning"], ["loops", "replaceable at equal priority"])
        self.assertEqual(animtable.flag_words("0x20001"), ["loops", "0x20000"])

    def test_priority_is_a_byte(self):
        doc = animtable.to_json(table())
        doc["sets"][0]["entries"][0]["priority"] = 300
        with self.assertRaises(ValueError):
            animtable.from_json(doc)

    def test_counts_recomputed_and_checked(self):
        doc = animtable.to_json(table())
        doc["sets"][0]["entries"].append({"handle": "lb_walk", "anim": "walk", "priority": 40, "flags": "0x5"})
        back = animtable.to_json(animtable.from_json(doc))
        self.assertEqual(len(back["sets"][0]["entries"]), 3)
        doc["sets"][0]["entries"][1]["variants"]["count"] = 5
        with self.assertRaises(ValueError):
            animtable.from_json(doc)


class NewClipModTests(unittest.TestCase):
    def test_new_clip_load_list_and_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            build_dsk(d / "data" / "streamed.dsk", [(name_hash("test_anim"), animation.ANIMATION, anim_record())])
            load_list = b"\n[animation]\n\nload test_anim\nload other_anim\n"
            build_dsk(d / "data" / "assets.dsk", [(name_hash("guy_table"), animtable.ANIMTABLE, table()),
                                                  (name_hash("layer_a"), aidata.LIST, load_list)])
            modbuild.new_project(d / "proj")
            a = animation.decode(anim_record())
            a.name = "my_new_run"
            (d / "proj" / "animations" / "new.glb").write_bytes(gltf.export(_model(), animations=[a]))
            (d / "proj" / "animations" / "new.json").write_text(json.dumps({"my_new_run": "test_anim"}))
            doc = animtable.to_json(table())
            doc["sets"][0]["entries"][0]["anim"] = "my_new_run"
            (d / "proj" / "animtables" / "guy_table.json").write_text(json.dumps(doc))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            self.assertEqual(sorted(result.written), ["assets.dsk", "streamed.dsk"])
            self.assertEqual(result.warnings, [])
            streamed = dsk.Archive(d / "out" / "streamed.dsk")
            recs = {r.key: streamed.read(r) for r in streamed.records}
            new = animation.decode(recs[name_hash("my_new_run")])
            self.assertEqual((new.name, new.frames), ("my_new_run", 3))
            self.assertEqual(recs[name_hash("test_anim")], anim_record())   # template untouched
            assets = dsk.Archive(d / "out" / "assets.dsk")
            arecs = {r.key: assets.read(r) for r in assets.records}
            self.assertIn(b"load test_anim\nload my_new_run\nload other_anim", arecs[name_hash("layer_a")])
            got = animtable.to_json(arecs[name_hash("guy_table")])
            self.assertEqual(got["sets"][0]["entries"][0]["anim"], "my_new_run")

    def test_new_table_from_base_with_added_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            civ = chunk(b"ucfb", chunk(b"atbl", chunk(b"NAME", b"civ\0") + chunk(b"SIZE", struct.pack("<2I", 0, 1))
                                       + chunk(b"aset", chunk(b"NAME", b"default\0") + chunk(b"SIZE", struct.pack("<I", 1))
                                               + entry("fb_subdued", "civ_subdued"))))
            load_list = b"\n[animtable]\n\nload civ\nload guy_table\n\n[animation]\n\nload test_anim\n"
            build_dsk(d / "data" / "assets.dsk", [(name_hash("guy_table"), animtable.ANIMTABLE, table()),
                                                  (name_hash("civ"), animtable.ANIMTABLE, civ),
                                                  (name_hash("humans"), aidata.LIST, load_list),
                                                  (name_hash("other"), aidata.LIST, b"\n[animtable]\n\nload guy_table\n")])
            modbuild.new_project(d / "proj")
            (d / "proj" / "animtables" / "my_civ.new.json").write_text(json.dumps(
                {"base": "civ", "fallback": "guy_table", "add": {"guy_table": ["fb_fid*", "lb_run"]}}))
            result = modbuild.build(d / "proj", d / "data", d / "out")
            assets = dsk.Archive(d / "out" / "assets.dsk")
            arecs = {r.key: assets.read(r) for r in assets.records}
            got = animtable.to_json(arecs[name_hash("my_civ")])
            self.assertEqual((got["name"], got["fallback"]), ("my_civ", "guy_table"))
            handles = [e["handle"] for e in got["sets"][0]["entries"]]
            self.assertEqual(handles, ["fb_subdued", "lb_run", "fb_fidget"])
            self.assertEqual(got["variants"], ["fidget_a", "fidget_b"])
            self.assertEqual(got["sets"][0]["entries"][2]["variants"], {"start": 0, "count": 2})
            self.assertEqual(arecs[name_hash("civ")], civ)                        # the game's table untouched
            self.assertIn(b"load civ\nload guy_table\nload my_civ", arecs[name_hash("humans")])
            self.assertNotIn(b"my_civ", arecs.get(name_hash("other"), b"\n[animtable]\n\nload guy_table\n"))
            # the variant clips aren't loaded beside civ
            self.assertTrue(any("fidget_a" in w for w in result.warnings), result.warnings)
            # a fallback on another new table
            (d / "proj" / "animtables" / "my_press.new.json").write_text(json.dumps(
                {"base": "civ", "fallback": "my_civ"}))
            modbuild.build(d / "proj", d / "data", d / "out3")
            arecs = {r.key: dsk.Archive(d / "out3" / "assets.dsk").read(r)
                     for r in dsk.Archive(d / "out3" / "assets.dsk").records}
            self.assertEqual(animtable.to_json(arecs[name_hash("my_press")])["fallback"], "my_civ")
            self.assertIn(b"load my_civ\nload my_press", arecs[name_hash("humans")])
            (d / "proj" / "animtables" / "my_press.new.json").unlink()
            (d / "proj" / "animtables" / "my_civ.new.json").write_text(json.dumps(
                {"base": "civ", "add": {"guy_table": ["fb_nothing_*"]}}))
            with self.assertRaises(ValueError):
                modbuild.build(d / "proj", d / "data", d / "out2")

    def test_new_name_clash_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "data").mkdir()
            build_dsk(d / "data" / "streamed.dsk", [(name_hash("test_anim"), animation.ANIMATION, anim_record())])
            modbuild.new_project(d / "proj")
            a = animation.decode(anim_record())
            (d / "proj" / "animations" / "x.glb").write_bytes(gltf.export(_model(), animations=[a]))
            (d / "proj" / "animations" / "new.json").write_text(json.dumps({"test_anim": "test_anim"}))
            with self.assertRaises(ValueError):
                modbuild.build(d / "proj", d / "data", d / "out")


if __name__ == "__main__":
    unittest.main()
