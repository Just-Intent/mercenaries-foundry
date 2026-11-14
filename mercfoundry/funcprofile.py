"""Passive function mapper: which game functions run while the player does what.

The port stores the guest address of every translated function it enters in
a host global, `g_recomp_current_func`. Foundry Console reads that variable
from a thread of its own a few thousand times a second, counts hits per
address under a label ("tank", "onfoot", "gameplay", "menu"; see
docs/FUNCTION_MAP.md), and writes the counts to
plugin-settings/foundry-console/samples/<session>.tsv.

This module
- finds the variable in the exe's linker map (its address changes with every
  port build) and writes the hand-off file the plugin reads at startup,
  together with the map's timestamp, which the plugin checks against the
  running exe's PE header so it never reads a stale address;
- reads sample files and ranks the functions that turn up more often under
  one label than under others, described from the function map.

A sample says which function was entered last, on any guest thread, when the
sampler looked. It finds candidates for naming, not names.
"""
import bisect
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

SYMBOL = "g_recomp_current_func"
PLUGIN_ID = "foundry-console"
HANDOFF = "sampler-address.ini"
SETTINGS = "foundry-console.ini"
EXE = "mercenaries_recomp.exe"
MAP = "mercenaries_recomp.map"

DEFAULT_SETTINGS = """\
; Foundry Console settings. Read when the game starts.
[Sampler]
; 1: sample which game function runs (the passive function mapper), from
; the start. Foundry_Sample(1) / Foundry_Sample(0) in the Lua box (F7)
; turns it on and off while playing. See docs/FUNCTION_MAP.md.
Enabled=0
; Reads per second (100 to 20000).
Rate=8000
; Seconds between writes to samples\\<session>.tsv.
FlushSeconds=10
"""


@dataclass
class LinkerMap:
    timestamp: int          # "Timestamp is <hex>": the PE header's TimeDateStamp
    base: int               # "Preferred load address is <hex>"
    symbols: dict = field(default_factory=dict)   # name -> virtual address

    def rva(self, name):
        return self.symbols[name] - self.base


_MAP_LINE = re.compile(r"^\s*[0-9A-Fa-f]{4}:[0-9A-Fa-f]{8}\s+(\S+)\s+([0-9A-Fa-f]{16})\s")


def parse_map(text, wanted=(SYMBOL,)):
    """The timestamp, preferred base and the wanted symbols' addresses from an
    MSVC linker map. Raises ValueError if the header or a symbol is missing,
    or a symbol appears with two different addresses."""
    ts = re.search(r"^\s*Timestamp is ([0-9A-Fa-f]{1,8})\b", text, re.M)
    base = re.search(r"^\s*Preferred load address is ([0-9A-Fa-f]{1,16})\b", text, re.M)
    if not ts or not base:
        raise ValueError("not an MSVC linker map (no timestamp or preferred load address)")
    m = LinkerMap(int(ts.group(1), 16), int(base.group(1), 16))
    wanted = set(wanted)
    for line in text.splitlines():
        hit = _MAP_LINE.match(line)
        if not hit or hit.group(1) not in wanted:
            continue
        va = int(hit.group(2), 16)
        if m.symbols.setdefault(hit.group(1), va) != va:
            raise ValueError(f"{hit.group(1)} appears at two addresses in the map")
    missing = wanted - set(m.symbols)
    if missing:
        raise ValueError(f"the map has no {', '.join(sorted(missing))}")
    for name, va in m.symbols.items():
        if not 0 < va - m.base < 0x80000000:
            raise ValueError(f"{name} at 0x{va:X} isn't inside the image")
    return m


def pe_timestamp(path):
    """TimeDateStamp of a PE file's COFF header."""
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:2] != b"MZ":
        raise ValueError(f"{path} is not a Windows executable")
    pe, = struct.unpack_from("<I", head, 0x3C)
    if head[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path} has no PE header")
    return struct.unpack_from("<I", head, pe + 8)[0]


def handoff_text(linker_map):
    rva = linker_map.rva(SYMBOL)
    return ("; Written by mercfoundry (mod sync) from mercenaries_recomp.map. Don't edit.\r\n"
            "; Foundry Console samples the variable below only while the running exe's\r\n"
            "; PE timestamp equals Timestamp; a rebuilt port needs a new sync.\r\n"
            "[CurrentFunc]\r\n"
            f"Symbol={SYMBOL}\r\n"
            f"Rva=0x{rva:08X}\r\n"
            f"Timestamp=0x{linker_map.timestamp:08X}\r\n")


def read_handoff(text):
    """{'rva': int, 'timestamp': int} from a hand-off file (as the plugin reads it)."""
    vals = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith((";", "[")):
            k, v = (s.strip() for s in line.split("=", 1))
            vals[k.lower()] = v
    if vals.get("symbol") != SYMBOL:
        raise ValueError(f"the hand-off file isn't for {SYMBOL}")
    return {"rva": int(vals["rva"], 16), "timestamp": int(vals["timestamp"], 16)}


def settings_dir(game):
    return Path(game) / "plugin-settings" / PLUGIN_ID


def write_handoff(game):
    """Write plugin-settings/foundry-console/sampler-address.ini for the exe
    in game (the folder holding mercenaries_recomp.exe and its .map), and the
    default settings file if there is none. Returns a line saying what
    happened. Removes an old hand-off when the map is missing or doesn't
    match the exe, so the plugin never gets a wrong address."""
    game = Path(game)
    out = settings_dir(game) / HANDOFF
    map_path, exe = game / MAP, game / EXE

    def drop(why):
        if out.exists():
            out.unlink()
        return f"function sampler off: {why}"

    if not map_path.exists() or not exe.exists():
        return drop(f"no {MAP} beside {EXE}")
    try:
        lm = parse_map(map_path.read_text(encoding="utf-8", errors="replace"))
        stamp = pe_timestamp(exe)
    except (ValueError, OSError) as err:
        return drop(str(err))
    if stamp != lm.timestamp:
        return drop(f"{MAP} (timestamp 0x{lm.timestamp:08X}) is not from this {EXE} (0x{stamp:08X})")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = handoff_text(lm)
    if not out.exists() or out.read_text(encoding="utf-8", errors="replace") != text.replace("\r\n", "\n"):
        out.write_bytes(text.encode("ascii"))
    ini = out.parent / SETTINGS
    if not ini.exists():
        ini.write_bytes(DEFAULT_SETTINGS.replace("\n", "\r\n").encode("ascii"))
    return f"function sampler: {SYMBOL} at RVA 0x{lm.rva(SYMBOL):08X} (exe 0x{lm.timestamp:08X})"


# ---------- sample files ----------

def read_samples(paths):
    """{label: {address: count}} summed over sample files.

    Lines are label<TAB>address<TAB>count; '#' lines and the column header
    are skipped, as is address 0 (nothing entered yet)."""
    out = {}
    for p in paths:
        for n, line in enumerate(Path(p).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.rstrip("\r").split("\t")
            if cols[0] == "label":
                continue
            if len(cols) != 3:
                raise ValueError(f"{p}:{n}: expected label, address and count")
            try:
                addr, count = int(cols[1], 16), int(cols[2])
            except ValueError:
                raise ValueError(f"{p}:{n}: bad address or count") from None
            if addr:
                bucket = out.setdefault(cols[0], {})
                bucket[addr] = bucket.get(addr, 0) + count
    return out


@dataclass
class Row:
    address: int
    count: int            # samples under the label
    other: int            # samples under the labels compared against
    rate: float           # per 1,000 samples under the label
    other_rate: float     # per 1,000 samples under the others
    ratio: float          # how many times more often (smoothed)


def merge(samples, labels):
    total = {}
    for lab in labels:
        for a, c in samples.get(lab, {}).items():
            total[a] = total.get(a, 0) + c
    return total


def rank(samples, label, against=None, min_count=20, only=False, fold=None):
    """Functions ranked by how much more often they appear under label than
    under the others (the against labels, or every other label).

    Each count is divided by its side's total samples. The ratio adds half a
    sample to both counts, so a function never seen on the other side ranks
    by how often it was seen, not as infinity. Functions no more frequent
    under the label (per sample) are left out. only: just the functions never
    seen on the other side. fold: address -> function start, applied first."""
    if label not in samples:
        raise ValueError(f"no samples labelled {label!r}; labels: {', '.join(sorted(samples)) or 'none'}")
    others = list(against) if against else [k for k in samples if k != label]
    missing = [k for k in others if k not in samples]
    if missing:
        raise ValueError(f"no samples labelled {', '.join(map(repr, missing))}")
    mine, theirs = samples[label], merge(samples, others)
    if fold:
        mine, theirs = _fold(mine, fold), _fold(theirs, fold)
    n_mine, n_theirs = sum(mine.values()), sum(theirs.values())
    if not n_theirs:
        raise ValueError("nothing to compare against: record samples under another label too")
    rows = []
    for a, c in mine.items():
        o = theirs.get(a, 0)
        if c < min_count or (only and o):
            continue
        ratio = ((c + 0.5) / n_mine) / ((o + 0.5) / n_theirs)
        if c * n_theirs <= o * n_mine:
            continue                      # not more frequent under the label
        rows.append(Row(a, c, o, 1000.0 * c / n_mine, 1000.0 * o / n_theirs, ratio))
    rows.sort(key=lambda r: (-r.ratio, -r.count, r.address))
    return rows, n_mine, n_theirs


def _fold(counts, fold):
    out = {}
    for a, c in counts.items():
        a = fold(a)
        out[a] = out.get(a, 0) + c
    return out


# ---------- describing the results ----------

class Describer:
    """Names, files, sections, callers and callees for addresses, from the
    function map, the port's analysis (functions.json) and default.xbe."""

    def __init__(self, doc, analysis=None, xbe=None):
        self.entries = {int(e["address"], 16): e for e in doc.get("functions", [])}
        self.names = {a: e["names"][0] for a, e in self.entries.items() if e.get("names")}
        self.funcs = {int(f["start"], 16): f for f in (analysis or [])}
        self.starts = sorted(self.funcs)
        self.xbe = xbe

    def function_start(self, addr):
        """The start of the function holding addr (addr itself if unknown)."""
        if addr in self.funcs or not self.starts:
            return addr
        k = bisect.bisect_right(self.starts, addr) - 1
        if k >= 0:
            f = self.funcs[self.starts[k]]
            if self.starts[k] <= addr < int(f["end"], 16):
                return self.starts[k]
        return addr

    def section(self, addr):
        if self.xbe is not None:
            s = self.xbe.section_of(addr)
            if s:
                return s
        f = self.funcs.get(addr)
        return f.get("section") if f else None

    def describe(self, addr, limit=4):
        e = self.entries.get(addr, {})
        f = self.funcs.get(addr, {})
        out = {"name": self.names.get(addr), "file": e.get("file"), "file_evidence": e.get("file_evidence"),
               "section": self.section(addr)}
        for key, src in (("callers", "called_by"), ("callees", "calls_to")):
            named = []
            for x in f.get(src, []):
                x = int(x, 16)
                if x in self.names and self.names[x] not in named:
                    named.append(self.names[x])
            out[key] = named[:limit]
            out[key + "_more"] = max(0, len(named) - limit)
            out[key + "_total"] = len(f.get(src, []))
        return out


def report(rows, describer, label, against_name, n_mine, n_theirs, limit=40):
    """Report lines for ranked rows."""
    lines = [f"{label}: {n_mine:,} samples; {against_name}: {n_theirs:,} samples",
             f"{'address':<10}  {label + ' /1k':>9}  {'other /1k':>9}  {'ratio':>7}  name [file] (section)"]
    for r in rows[:limit]:
        d = describer.describe(r.address)
        ratio = f"{r.ratio:7.1f}" if r.other else "   only"
        bits = [d["name"] or "(unnamed)"]
        if d["file"]:
            bits.append(f"[{d['file']}{', between' if d['file_evidence'] == 'between' else ''}]")
        if d["section"] and d["section"] != ".text":
            bits.append(f"({d['section']} library)")
        lines.append(f"0x{r.address:08X}  {r.rate:9.2f}  {r.other_rate:9.2f}  {ratio}  {' '.join(bits)}"
                     f"   n={r.count}")
        for key, word in (("callers", "called by"), ("callees", "calls")):
            if d[key]:
                more = f", +{d[key + '_more']} more named" if d[key + "_more"] else ""
                lines.append(f"{'':12}{word}: {', '.join(d[key])}{more} (of {d[key + '_total']})")
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more (use --limit)")
    if not rows:
        lines.append("no functions pass the filters (lower --min-count, or record longer)")
    return lines


def load_analysis(path):
    p = Path(path) / "functions.json" if path else None
    if p and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None
