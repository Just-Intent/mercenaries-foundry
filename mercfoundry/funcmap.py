"""Function map: names and source files for the game's code, with evidence.

Everything here is derived from the retail executable (default.xbe) and
the port's own analysis of it (functions.json and strings.json from
Mercenaries Recompiled's disassembler, MIT):

- Lua bindings. The executable registers its script commands from tables
  of {name, function} pairs in .rdata, ending in {0, 0}. Each entry names
  the C function behind a script command exactly. Names: Lua_<Command>
  (e.g. Lua_Mission_Spawn), Lua_Actor_<Name> for the table registered
  with the "Actor_" prefix, LuaLib_<library>_<name> for Lua's own
  libraries.
- Source files. The game keeps assert strings like
  ".\\Source\\RsActor.cpp [582]"; a function that references one was
  compiled from that file ("direct"). A run of unattributed functions
  between two functions from the same file, in the same section, is
  marked "between" (the linker keeps each file's functions together).
- Hand-curated names (map/manual.json), each with its evidence.

The generated map (map/functions.json) holds only functions with a name
or a file. Addresses are the executable's virtual addresses, the same as
the port's sub_XXXXXXXX names.
"""
import hashlib
import json
import re
import struct
from pathlib import Path

# Registration tables found in .rdata: (address, prefix for names, kind, evidence)
TABLES = {
    0x2ED308: ("", "Lua", "global script commands"),
    0x2EDCA8: ("Actor_", "Lua", "registered with the \"Actor_\" prefix (string at 0x2F0018, used at 0x1128A2)"),
    0x2F0A64: ("ActorMeta_", "Lua", "metatable for actor values (__gc, __index, __eq), set up at 0x120DC8"),
    0x2FB1E0: ("table_", "LuaLib", "Lua table library (\"table\" pushed at 0x1DA5CC)"),
    0x2FB2B8: ("base_", "LuaLib", "Lua base library (registered into _G at 0x1DB77B)"),
    0x2FB388: ("coroutine_", "LuaLib", "Lua coroutine library (\"coroutine\" pushed at 0x1DB832)"),
}


class Xbe:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        d = self.data
        if d[:4] != b"XBEH":
            raise ValueError(f"{path} is not an Xbox executable")
        self.base, = struct.unpack_from("<I", d, 0x104)
        nsec, sec_addr = struct.unpack_from("<II", d, 0x11C)
        self.sections = []
        for i in range(nsec):
            o = sec_addr - self.base + i * 56
            _flags, va, vs, ra, rs, name_addr = struct.unpack_from("<6I", d, o)
            name = d[name_addr - self.base:name_addr - self.base + 16].split(b"\0")[0].decode("latin-1")
            self.sections.append((name, va, vs, ra, rs))

    def section_of(self, va):
        for name, sva, vs, ra, rs in self.sections:
            if sva <= va < sva + max(vs, rs):
                return name
        return None

    def read(self, va, n):
        for name, sva, vs, ra, rs in self.sections:
            if sva <= va < sva + rs:
                return self.data[ra + va - sva:ra + va - sva + n]
        return None

    def cstr(self, va, limit=96):
        b = self.read(va, limit)
        if not b or b"\0" not in b:
            return None
        s = b.split(b"\0", 1)[0]
        if not s or not all(32 < c < 127 for c in s):
            return None
        return s.decode("latin-1")

    def sha1(self):
        return hashlib.sha1(self.data).hexdigest()


def lua_tables(xbe):
    """[(table address, [(name, function address)])] for every {name, fn}
    table in .rdata (at least two entries, ending in {0, 0})."""
    sec = next(s for s in xbe.sections if s[0] == ".rdata")
    _, va0, _, ra, rs = sec
    d = xbe.data
    out, i = [], 0
    while i < rs - 8:
        name_p, fn_p = struct.unpack_from("<II", d, ra + i)
        if xbe.section_of(fn_p) == ".text" and xbe.section_of(name_p) == ".rdata" and xbe.cstr(name_p):
            entries, j = [], i
            while j < rs - 8:
                a, b = struct.unpack_from("<II", d, ra + j)
                if a == 0 and b == 0:
                    break
                if xbe.section_of(b) != ".text" or not xbe.cstr(a):
                    entries = None
                    break
                entries.append((xbe.cstr(a), b))
                j += 8
            if entries and len(entries) >= 2:
                out.append((va0 + i, entries))
                i = j + 8
                continue
        i += 4
    return out


ASSERT = re.compile(r"Source.([A-Za-z0-9_]+\.(?:cpp|c|h)) \[(\d+)\]")


def build(xbe_path, analysis_dir, manual_path=None):
    """Build the map document."""
    xbe = Xbe(xbe_path)
    analysis = Path(analysis_dir)
    funcs = json.loads((analysis / "functions.json").read_text(encoding="utf-8"))
    strings = json.loads((analysis / "strings.json").read_text(encoding="utf-8"))
    ranges = sorted((int(f["start"], 16), int(f["end"], 16), f.get("section", ".text")) for f in funcs)
    starts = [r[0] for r in ranges]

    import bisect

    def containing(addr):
        k = bisect.bisect_right(starts, addr) - 1
        if k >= 0 and ranges[k][0] <= addr < max(ranges[k][1], ranges[k][0] + 1):
            return ranges[k][0]
        return None

    entries = {}

    def entry(addr):
        return entries.setdefault(addr, {"address": f"0x{addr:08X}", "names": [], "evidence": []})

    # Lua bindings
    unknown_tables = []
    for tva, items in lua_tables(xbe):
        if tva not in TABLES:
            unknown_tables.append(tva)
            continue
        prefix, kind, why = TABLES[tva]
        for k, (name, fn) in enumerate(items):
            full = f"{kind}_{prefix}{name}"
            e = entry(fn)
            if full not in e["names"]:
                e["names"].append(full)
            e["evidence"].append(f"entry {k} of Lua table 0x{tva:06X} ({why})")
            if kind == "Lua" and prefix in ("", "Actor_"):
                e.setdefault("commands", []).append(prefix + name)

    # Source files from assert strings
    direct = {}
    for s in strings:
        m = ASSERT.search(s["string"])
        if not m:
            continue
        for ref in s.get("referenced_from", []):
            f = containing(int(ref, 16))
            if f is None:
                continue
            direct.setdefault(f, {}).setdefault(m.group(1), set()).add(int(m.group(2)))
    for f, files in direct.items():
        best = max(files, key=lambda n: (len(files[n]), n.endswith(".cpp")))
        e = entry(f)
        e["file"] = best
        e["file_evidence"] = "direct"
        e["lines"] = sorted(files[best])
    # Between two functions from the same file, in the same section.
    order = [r[0] for r in ranges]
    section = {r[0]: r[2] for r in ranges}
    known = [k for k, a in enumerate(order) if a in direct]
    for k0, k1 in zip(known, known[1:]):
        a0, a1 = order[k0], order[k1]
        if entries[a0]["file"] != entries[a1]["file"] or section[a0] != section[a1]:
            continue
        for k in range(k0 + 1, k1):
            a = order[k]
            if section[a] != section[a0]:
                break
            e = entry(a)
            if "file" not in e:
                e["file"] = entries[a0]["file"]
                e["file_evidence"] = "between"

    # Hand-curated
    curated = {"functions": [], "globals": []}
    if manual_path and Path(manual_path).exists():
        curated = json.loads(Path(manual_path).read_text(encoding="utf-8"))
    manual = curated.get("functions", [])
    starts_set = set(starts)
    for m in manual:
        if int(m["address"], 16) not in starts_set:
            raise ValueError(f"map/manual.json: {m['address']} ({m.get('name')}) isn't a function start "
                             "in the port's analysis")
    for m in manual:
        addr = int(m["address"], 16)
        e = entry(addr)
        if m.get("name"):
            e["names"] = [m["name"]] + [n for n in e["names"] if n != m["name"]]
        e["evidence"].append(m["evidence"])
        for key in ("notes", "signature"):
            if m.get(key):
                e[key] = m[key]

    out = []
    for addr in sorted(entries):
        e = entries[addr]
        if not e["names"]:
            del e["names"]
        if not e["evidence"]:
            del e["evidence"]
        out.append(e)
    doc = {
        "xbe": {"sha1": xbe.sha1(), "title_note": "US retail Mercenaries: Playground of Destruction"},
        "counts": {
            "functions_in_analysis": len(funcs),
            "named": sum(1 for e in out if e.get("names")),
            "with_file": sum(1 for e in out if e.get("file")),
            "file_direct": sum(1 for e in out if e.get("file_evidence") == "direct"),
            "lua_commands": sum(len(e.get("commands", [])) for e in out),
            "manual": len(manual),
            "globals": len(curated.get("globals", [])),
        },
        "functions": out,
        "globals": curated.get("globals", []),
    }
    if unknown_tables:
        doc["unclassified_tables"] = [f"0x{t:06X}" for t in unknown_tables]
    return doc


def load(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc


def name_of(e):
    return (e.get("names") or [None])[0]


def find(doc, text):
    text = text.lower()
    out = []
    for e in doc["functions"]:
        hay = " ".join([e["address"], *(e.get("names") or []), e.get("file", ""), " ".join(e.get("commands", [])),
                        e.get("notes", "")]).lower()
        if text in hay:
            out.append(e)
    return out


def ghidra_names(doc):
    """{address: name} in the port's ghidra_names.json format."""
    return {e["address"]: name_of(e) for e in doc["functions"] if name_of(e)}


def header(doc):
    """C header with an address constant per named function."""
    lines = ["/* Generated by `python -m mercfoundry funcmap header`. Addresses in the",
             " * retail executable (the port's sub_XXXXXXXX names). Do not edit. */",
             "#pragma once", ""]
    for e in doc["functions"]:
        n = name_of(e)
        if not n:
            continue
        ident = re.sub(r"[^A-Za-z0-9_]", "_", n)
        note = f"  /* {e['signature']} */" if e.get("signature") else (f"  /* {e['file']} */" if e.get("file") else "")
        lines.append(f"#define MF_FN_{ident} 0x{int(e['address'], 16):08X}u{note}")
    if doc.get("globals"):
        lines.append("")
    for g in doc.get("globals", []):
        ident = re.sub(r"[^A-Za-z0-9_]", "_", g["name"])
        lines.append(f"#define MF_VAR_{ident} 0x{int(g['address'], 16):08X}u")
    return "\n".join(lines) + "\n"


def show(doc, query, analysis_dir=None):
    """Lines describing one function (by name or address), with its callers
    and callees named where the map knows them."""
    hits = [e for e in doc["functions"] if query.lower() in [n.lower() for n in e.get("names", [])]]
    if not hits:
        try:
            a = int(query, 16)
            hits = [e for e in doc["functions"] if int(e["address"], 16) == a] or [{"address": f"0x{a:08X}"}]
        except ValueError:
            return None
    e = hits[0]
    names = {int(x["address"], 16): name_of(x) for x in doc["functions"] if name_of(x)}
    lines = [f"{e['address']}  {' / '.join(e.get('names', [])) or '(unnamed)'}"]
    for key in ("signature", "commands", "file", "file_evidence", "lines", "notes"):
        if e.get(key):
            v = e[key]
            lines.append(f"  {key}: {', '.join(map(str, v)) if isinstance(v, list) else v}")
    for ev in e.get("evidence", []):
        lines.append(f"  evidence: {ev}")
    if analysis_dir:
        funcs = json.loads((Path(analysis_dir) / "functions.json").read_text(encoding="utf-8"))
        f = next((x for x in funcs if int(x["start"], 16) == int(e["address"], 16)), None)
        if f:
            def label(a):
                a = int(a, 16)
                return f"0x{a:08X} {names.get(a, '')}".rstrip()
            lines.append(f"  size: {f.get('size')} bytes")
            lines.append("  calls: " + ("; ".join(label(c) for c in f.get("calls_to", [])) or "-"))
            called = f.get("called_by", [])
            lines.append(f"  called by ({len(called)}): " + ("; ".join(label(c) for c in called[:12]) or "-")
                         + (" ..." if len(called) > 12 else ""))
    return lines
