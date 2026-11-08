import json
import random
import struct
import tempfile
import unittest
from pathlib import Path

from mercfoundry import dsk, registry, script, ucfb, world
from mercfoundry.hashing import name_hash
from tests.test_dsk import build, chunk


def wrap(inner, body):
    return chunk(b"ucfb", chunk(inner, body))


def table(strings):
    out = struct.pack("<I", len(strings))
    for s in strings:
        out += struct.pack("<II", name_hash(s), len(s)) + s.encode() + b"\0"
    return out


def world_record():
    strings = ["base_layer", "jeep_model", "jeep_template", "GeometryFile", "jeep.msh", "Faction", "China"]
    h = name_hash
    prop = struct.pack("<6I", h("jeep_model"), h("jeep_template"), h("GeometryFile"), h("jeep.msh"), h("Faction"), h("China"))
    body = chunk(b"NAME", b"base_layer\0") + chunk(b"TABL", table(strings)) + chunk(b"inst", chunk(b"PROP", prop))
    body += chunk(b"XTRA", b"\x01\x02\x03")
    return wrap(b"wrld", body)


def text_entry(tag, name, data, tail=b"\0"):
    return chunk(tag, chunk(b"INFO", b"name\0" + name.encode() + b"\0" + tail) + chunk(b"DATA", data))


def text_record():
    # Retail layout: no terminators, header INFO with num_components.
    jeep = text_entry(b"BIN_", "jeep", "Jeep".encode("utf-16-le"), tail=b"")
    tank = text_entry(b"BIN_", "tank", "Tank\0".encode("utf-16-le"), tail=b"")  # terminated variant
    blob = text_entry(b"BIN_", "flags", b"\x01\x02\x03", tail=b"")
    veh = text_entry(b"SCPE", "veh", jeep + tank + blob, tail=b"")
    return wrap(b"reg_", chunk(b"INFO", b"name\0english\0num_components\x005\0") + veh)


class CompressionTests(unittest.TestCase):
    def check(self, data):
        packed = script.compress(data)
        self.assertEqual(script.decompress(packed, len(data)), data)
        return packed

    def test_edge_cases(self):
        for data in [b"", b"a", b"ab", b"abc", b"aaaa", b"a" * 1000, b"ab" * 3000,
                     bytes(range(256)) * 20, b"x" * 257 + b"y" * 300]:
            self.check(data)

    def test_far_and_near_matches(self):
        rng = random.Random(7)
        noise = bytes(rng.randrange(256) for _ in range(5000))
        self.check(noise[:4096] + noise[:300])     # distance exactly 4096
        self.check(noise[:200] + noise[:3])        # short count 3
        self.check(noise[:300] + noise[:3])        # long count 3 (extension byte)
        self.check(noise[:300] + noise[:18] + noise[:19])

    def test_random_text_shrinks(self):
        rng = random.Random(1)
        words = [b"local", b"function", b"end", b"if", b"then", b"Mission", b"(", b")", b" ", b"\n"]
        data = b"".join(rng.choice(words) for _ in range(20000))
        self.assertLess(len(self.check(data)), len(data) // 2)

    def test_rejects_bad_streams(self):
        packed = script.compress(b"hello world hello world")
        with self.assertRaises(ValueError):
            script.decompress(packed, 5)
        with self.assertRaises(ValueError):
            script.decompress(packed[:-2], 23)


class ScriptTests(unittest.TestCase):
    def test_new_roundtrip_compressed_and_stored(self):
        for compressed in (True, False):
            rec = script.encode(script.new("mission01", "print('hi')\n", compressed))
            got = script.decode(rec)
            self.assertEqual(got.name, "mission01")
            self.assertEqual(got.source, "print('hi')\n")
            self.assertEqual(got.compressed, compressed)
            self.assertEqual(ucfb.record_name(rec), "mission01")

    def test_edit_keeps_info_byte(self):
        orig = script.Script("m", b"x = 1\0", b"\x07\0\0\0\0", False)
        edited = script.decode(script.encode(script.with_source(orig, "x = 2")))
        self.assertEqual(edited.info[0], 7)
        self.assertEqual(edited.text, b"x = 2\0")

    def test_exact_rebuild_of_stored_script(self):
        rec = wrap(b"scr_", chunk(b"NAME", b"m\0") + chunk(b"INFO", b"\x03\0\0\0\0") + chunk(b"BODY", b"abc"))
        self.assertEqual(script.encode(script.decode(rec)), rec)


class WorldTests(unittest.TestCase):
    def test_decode(self):
        doc = world.to_json(world_record())
        self.assertEqual(doc["name"], "base_layer")
        inst = [c for c in doc["chunks"] if c["tag"] == "inst"][0]
        self.assertEqual(inst["model"], "jeep_model")
        self.assertEqual(inst["properties"], [["GeometryFile", "jeep.msh"], ["Faction", "China"]])

    def test_exact_rebuild(self):
        rec = world_record()
        doc = json.loads(json.dumps(world.to_json(rec)))
        self.assertEqual(world.from_json(doc), rec)

    def test_edit_adds_string(self):
        doc = world.to_json(world_record())
        inst = [c for c in doc["chunks"] if c["tag"] == "inst"][0]
        inst["properties"][1][1] = "Allies"
        back = world.to_json(world.from_json(doc))
        self.assertIn("Allies", back["strings"])
        inst = [c for c in back["chunks"] if c["tag"] == "inst"][0]
        self.assertEqual(inst["properties"][1], ["Faction", "Allies"])

    def test_bad_table_hash(self):
        bad = struct.pack("<III", 1, 123, 3) + b"abc\0"
        with self.assertRaises(ValueError):
            world.read_table(bad)


class TextTests(unittest.TestCase):
    def test_strings(self):
        self.assertEqual(registry.to_json(text_record())["strings"], {"veh.jeep": "Jeep", "veh.tank": "Tank"})

    def test_exact_rebuild(self):
        rec = text_record()
        self.assertEqual(registry.Registry(rec).to_bytes(), rec)

    def test_edit(self):
        doc = registry.to_json(text_record())
        doc["strings"]["veh.jeep"] = "Jeep (modded) é"
        out = registry.from_json(doc, text_record())
        self.assertEqual(registry.to_json(out)["strings"]["veh.jeep"], "Jeep (modded) é")
        self.assertIn(b"\x01\x02\x03", out)  # non-text value untouched

    def test_terminator_kept_as_found(self):
        reg = registry.Registry(text_record())
        reg.apply({"veh.jeep": "Jeep 2", "veh.tank": "Tank 2"})
        nodes = dict(reg.walk())
        self.assertEqual(nodes["veh.jeep"].field(b"DATA"), "Jeep 2".encode("utf-16-le"))
        self.assertEqual(nodes["veh.tank"].field(b"DATA"), "Tank 2\0".encode("utf-16-le"))

    def test_add_strings_and_scopes(self):
        reg = registry.Registry(text_record())
        changed, added = reg.apply({"veh.boat": "Boat", "mod.new.hello": "Hi"})
        self.assertEqual((changed, added), (0, 4))  # 2 values + 2 new scopes
        again = registry.Registry(reg.to_bytes())
        self.assertEqual(again.strings()["veh.boat"], "Boat")
        self.assertEqual(again.strings()["mod.new.hello"], "Hi")
        self.assertIn(b"num_components\x009\x00", again.to_bytes())


class PaddingTests(unittest.TestCase):
    def test_unpadded_last_child_kept(self):
        body = chunk(b"AAAA", b"1234") + struct.pack("<4sI", b"BBBB", 1) + b"z"
        items, pad_last = ucfb.split(body)
        self.assertFalse(pad_last)
        self.assertEqual(ucfb.pack_children(items, pad_last), body)


class WorkflowTests(unittest.TestCase):
    def test_extract_decode_edit_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            scr = script.encode(script.new("mission", "Mission = 1\n"))
            recs = [
                (name_hash("mission"), name_hash("script"), scr),
                (name_hash("base_layer"), name_hash("world"), world_record()),
                (name_hash("english"), name_hash("registry"), text_record()),
                (1, 2, b"\xff\xff"),
            ]
            build(d / "a.dsk", recs)
            m = dsk.extract(d / "a.dsk", d / "x", decode=True)
            decoded = {e["type"]: d / "x" / e["decoded"] for e in m["records"] if e.get("decoded")}
            self.assertEqual(set(decoded), {"script", "world", "registry"})

            # Untouched: rebuild changes nothing and the archive is identical.
            self.assertEqual(dsk.rebuild(d / "x"), [])
            dsk.pack(d / "x", d / "same.dsk")
            self.assertEqual((d / "a.dsk").read_bytes(), (d / "same.dsk").read_bytes())

            decoded["script"].write_text("Mission = 2\n", encoding="latin-1")
            doc = json.loads(decoded["registry"].read_text(encoding="utf-8"))
            doc["strings"]["veh.tank"] = "Big Tank"
            decoded["registry"].write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(len(dsk.rebuild(d / "x")), 2)
            dsk.pack(d / "x", d / "b.dsk")

            arc = dsk.Archive(d / "b.dsk")
            by_kind = {r.kind: arc.read(r) for r in arc.records}
            self.assertEqual(script.decode(by_kind[name_hash("script")]).source, "Mission = 2\n")
            self.assertEqual(registry.to_json(by_kind[name_hash("registry")])["strings"]["veh.tank"], "Big Tank")
            _, _, changed = dsk.diff(d / "a.dsk", d / "b.dsk")
            self.assertEqual(len(changed), 2)


if __name__ == "__main__":
    unittest.main()
