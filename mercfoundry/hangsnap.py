"""hangsnap: what a frozen mercenaries_recomp.exe is doing.

Runs tools/hangsnap/hangsnap.exe against the game and names what it finds:

- every native thread's stack, as functions of the port's executable
  (from its linker map, mercenaries_recomp.map) or as <dll>+offset;
- the guest (Xbox) registers and the port's trace of recently entered
  guest functions, from the port's register/trace globals block;
- guest addresses (recompiled functions are named sub_XXXXXXXX after their
  Xbox address) named from map/functions.json where known, with the XBE
  section they're in (.text for the game, D3D, DSOUND, XACTENG... for the
  Xbox libraries linked into it), read from default.xbe's headers.

Nothing here is hard-coded to one build of the port: the globals' RVAs and
offsets come from the linker map that sits next to the exe.
"""
import ctypes
import re
import struct
import subprocess
import sys
from bisect import bisect_right
from pathlib import Path

EXE_NAME = "mercenaries_recomp.exe"
MAP_NAME = "mercenaries_recomp.map"
TOOL_DIR = Path(__file__).resolve().parent.parent / "tools" / "hangsnap"
TOOL_EXE = TOOL_DIR / "out" / "hangsnap.exe"

# The port's register/trace block (xbox_memory_layout.obj): starts at
# g_xbox_mem_offset; we read through g_recomp_recent_game_funcs.
BLOCK_START = "g_xbox_mem_offset"
BLOCK_LAST = "g_recomp_recent_game_funcs"
# XBE sections that hold data, not code (DOLBY is DSP microcode the
# audio library uploads, $$XTIMAGE/$$XSIMAGE are title/save images).
NON_CODE_SECTIONS = {".rdata", ".data", ".bss", "DOLBY", ".XTLID"}
REGISTERS = ("g_eax", "g_ecx", "g_edx", "g_ebx", "g_esp", "g_esi", "g_edi")

_MAP_LINE = re.compile(
    r"^\s*([0-9a-fA-F]{4}):([0-9a-fA-F]{8})\s+(\S+)\s+([0-9a-fA-F]{16})\s+(f\s+)?(?:i\s+)?(\S.*)?$")
_GUEST_NAME = re.compile(r"^sub_([0-9A-Fa-f]{8})(?:_\w+)?$")


class LinkerMap:
    """Symbols of an MSVC linker map (.map): publics and statics."""

    def __init__(self, text):
        self.base = None
        self.timestamp = None
        self.symbols = {}          # name -> rva (first seen)
        funcs = []                 # (rva, name)
        data = []                  # (rva, name)
        for line in text.splitlines():
            if self.base is None and "Preferred load address is" in line:
                self.base = int(line.split()[-1], 16)
                continue
            if self.timestamp is None and line.strip().startswith("Timestamp is"):
                self.timestamp = int(line.split()[2], 16)
                continue
            m = _MAP_LINE.match(line)
            if not m or self.base is None:
                continue
            name, va, is_func = m.group(3), int(m.group(4), 16), bool(m.group(5))
            if va < self.base:
                continue
            rva = va - self.base
            self.symbols.setdefault(name, rva)
            (funcs if is_func else data).append((rva, name))
        if self.base is None:
            raise ValueError("not a linker map: no preferred load address")
        funcs.sort()
        data.sort()
        self._func_rvas = [r for r, _ in funcs]
        self._func_names = [n for _, n in funcs]
        self._data = data

    @classmethod
    def load(cls, path):
        return cls(Path(path).read_text(encoding="latin-1"))

    def rva(self, name):
        return self.symbols[name]

    def function_at(self, rva):
        """(name, offset) of the function containing rva, or None."""
        i = bisect_right(self._func_rvas, rva) - 1
        if i < 0:
            return None
        return self._func_names[i], rva - self._func_rvas[i]

    def guest_starts(self):
        """Guest addresses of every recompiled function (sub_XXXXXXXX symbols)."""
        return sorted({a for a in map(guest_address, self._func_names) if a is not None})

    def data_after(self, name):
        """The data symbol laid out right after `name`: (name, rva), or None."""
        rva = self.symbols[name]
        for r, n in self._data:
            if r > rva:
                return n, r
        return None

    def block_layout(self, start=BLOCK_START, last=BLOCK_LAST):
        """(start rva, size, {symbol: offset}) for the data symbols from
        `start` through the end of `last`."""
        s = self.rva(start)
        nxt = self.data_after(last)
        end = nxt[1] if nxt else self.rva(last) + 4
        offsets = {n: r - s for r, n in self._data if s <= r < end}
        return s, end - s, offsets


def guest_address(name):
    """Xbox address of a recompiled function name (sub_0028E650, sub_0028E650_body), or None."""
    m = _GUEST_NAME.match(name)
    return int(m.group(1), 16) if m else None


class GuestNames:
    """Names for guest addresses: map/functions.json plus default.xbe sections."""

    def __init__(self, doc=None, sections=(), starts=()):
        self.names = {}
        for e in (doc or {}).get("functions", []):
            if e.get("names"):
                self.names[int(e["address"], 16)] = e["names"][0]
        # (name, va, size) from the XBE section table
        self.sections = [tuple(s[:3]) for s in sections]
        # every recompiled function's guest address (the port's sub_XXXXXXXX)
        self.starts = sorted(set(starts))

    @classmethod
    def load(cls, map_path=None, xbe_path=None, lmap=None):
        doc, sections = None, []
        if map_path and Path(map_path).exists():
            from . import funcmap
            doc = funcmap.load(map_path)
        if xbe_path and Path(xbe_path).exists():
            from .funcmap import Xbe
            sections = [(n, va, max(vs, rs)) for n, va, vs, ra, rs in Xbe(xbe_path).sections]
        return cls(doc, sections, lmap.guest_starts() if lmap else ())

    def containing(self, va):
        """Guest address of the recompiled function va is inside, or None."""
        i = bisect_right(self.starts, va) - 1
        return self.starts[i] if i >= 0 else None

    def describe(self, va):
        """label() for any guest code address, e.g. a return address on the
        guest stack: 0x00191680 (in 0x00191200 Name+0x480)."""
        start = self.containing(va)
        if start is None or start == va:
            return self.label(va)
        return f"0x{va:08X} (in {self.label(start)} +0x{va - start:X})"

    def section_of(self, va):
        for n, sva, size in self.sections:
            if sva <= va < sva + size:
                return n
        return None

    def is_code(self, va):
        """Inside a code section: .text or an Xbox library (D3D, DSOUND...).
        The XBE's executable flag can't tell: the retail .rdata and .data
        carry it too."""
        s = self.section_of(va)
        return s is not None and s not in NON_CODE_SECTIONS and not s.startswith("$$")

    def label(self, va):
        out = f"0x{va:08X}"
        if va in self.names:
            out += f" {self.names[va]}"
        s = self.section_of(va)
        if s and s != ".text":
            out += f" [{s}]"
        return out


def parse_snapshot(text):
    """hangsnap.exe output -> dict(base, size, globals_rva, globals (bytes),
    guest_esp, guest_stack (words), threads [(id, description, [(module, offset)])])."""
    snap = {"base": None, "size": None, "globals_rva": None, "globals": b"",
            "guest_esp": None, "guest_stack": [], "threads": []}
    section, words = None, []

    def flush():
        if section == "globals":
            snap["globals"] = struct.pack(f"<{len(words)}I", *words)
        elif section == "guest-stack":
            snap["guest_stack"] = list(words)

    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            flush()
            section, words = None, []
            head, _, rest = line.partition(" ")
            if head == "base":
                b, s = rest.split()[:2]
                snap["base"], snap["size"] = int(b, 16), int(s, 16)
            elif head == "globals":
                section = "globals"
                snap["globals_rva"] = int(rest.split()[0], 16)
            elif head == "guest-stack":
                section = "guest-stack"
                snap["guest_esp"] = int(rest.split()[0], 16)
            elif head == "thread":
                tid, _, desc = rest.partition(" ")
                snap["threads"].append((int(tid), desc.strip(), []))
                section = "thread"
            continue
        item = line.strip()
        if section in ("globals", "guest-stack"):
            words.extend(int(w, 16) for w in item.split())
        elif section == "thread":
            mod, _, off = item.rpartition("+")
            snap["threads"][-1][2].append((mod, int(off, 16)))
    flush()
    return snap


def ring(words, idx):
    """Entries of a ring buffer written at words[idx % n], oldest first."""
    n = len(words)
    count = min(idx, n)
    return [words[(idx - count + i) % n] for i in range(count)]


def collapse(seq):
    """[(value, repeat count)] with consecutive repeats folded."""
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return [tuple(x) for x in out]


def native_frame(lmap, guest, module, offset):
    """One line naming a native stack frame."""
    if module != "exe":
        return f"{module}+0x{offset:X}"
    hit = lmap.function_at(offset)
    if not hit:
        return f"exe+0x{offset:X}"
    name, off = hit
    text = f"{name}+0x{off:X}"
    ga = guest_address(name)
    if ga is not None:
        text += f"  (guest {guest.label(ga)})"
    return text


def group_threads(threads):
    """[(thread ids, description, frames)] with identical stacks folded
    together, threads that run the port's own code first."""
    groups = {}
    for tid, desc, frames in threads:
        key = (desc, tuple(frames))
        groups.setdefault(key, []).append(tid)
    out = [(ids, desc, list(frames)) for (desc, frames), ids in groups.items()]
    out.sort(key=lambda g: not any(m == "exe" for m, _ in g[2]))   # stable: keeps snapshot order
    return out


def report(snap, lmap, guest, recent=32):
    """Readable lines for a parsed snapshot."""
    lines = []
    if snap["base"] is not None:
        lines.append(f"image base 0x{snap['base']:X}, size 0x{snap['size']:X}")
    g = snap["globals"]
    trace_lines = []
    if g:
        _start, _size, offs = lmap.block_layout()

        def word(name):
            o = offs.get(name)
            if o is None or o + 4 > len(g):
                return None
            return struct.unpack_from("<I", g, o)[0]

        def array(name, end_name):
            o, e = offs.get(name), offs.get(end_name)
            if o is None:
                return []
            e = e if e is not None and e > o else len(g)
            return list(struct.unpack_from(f"<{(min(e, len(g)) - o) // 4}I", g, o))

        regs = "  ".join(f"{r[2:]}={word(r):08X}" for r in REGISTERS if word(r) is not None)
        lines += ["", "guest registers:", f"  {regs}"]
        cur = word("g_recomp_current_func")
        if cur is not None:
            lines.append(f"guest current function: {guest.label(cur)}")
        t = trace_lines
        if word("g_recomp_entry_trace_enabled") == 0:
            t += ["", "(entry tracing is off right now, so the recent-function lists may be stale)"]
        idx = word("g_recomp_recent_func_idx")
        if idx is not None:
            funcs = ring(array("g_recomp_recent_funcs", "g_recomp_recent_func_idx"), idx)
            t += ["", f"guest functions entered most recently (oldest first, {idx} entries in all):"]
            t += [f"  {guest.label(va)}" + (f"  x{n}" if n > 1 else "") for va, n in collapse(funcs)] or ["  (none)"]
        gidx = word("g_recomp_recent_game_func_idx")
        if gidx is not None:
            gfuncs = ring(array(BLOCK_LAST, None), gidx)[-recent:]
            t += ["", f"game (not library) functions entered most recently (last {len(gfuncs)}, oldest first):"]
            t += [f"  {guest.label(va)}" for va in gfuncs] or ["  (none)"]

    for ids, desc, frames in group_threads(snap["threads"]):
        head = f"thread {ids[0]}" if len(ids) == 1 else \
            f"{len(ids)} threads with this stack ({', '.join(map(str, ids[:6]))}{', ...' if len(ids) > 6 else ''})"
        lines += ["", head + (f" ({desc})" if desc else "") + ":"]
        lines += [f"  {native_frame(lmap, guest, m, o)}" for m, o in frames] or ["  (no frames)"]

    lines += trace_lines
    if snap["guest_stack"]:
        lines += ["", f"guest stack at 0x{snap['guest_esp']:08X}, words that point into guest code:"]
        hits = [(i, w) for i, w in enumerate(snap["guest_stack"]) if guest.is_code(w)]
        lines += [f"  esp+0x{i * 4:03X}  {guest.describe(w)}" for i, w in hits] or ["  (none)"]
    return lines


# --- finding and running things -------------------------------------------

def find_game_pids():
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == EXE_NAME:
            pids.append(int(parts[1]))
    return pids


def process_path(pid):
    """Full path of a process's executable (Windows), or None."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value)
        return None
    finally:
        k32.CloseHandle(h)


def pe_timestamp(path):
    with open(path, "rb") as f:
        head = f.read(4096)
    pe, = struct.unpack_from("<I", head, 0x3C)
    return struct.unpack_from("<I", head, pe + 8)[0]


def map_timestamp(path):
    """The 'Timestamp is ...' value from a linker map's header, or None."""
    with open(path, encoding="latin-1") as f:
        for _ in range(10):
            line = f.readline().strip()
            if line.startswith("Timestamp is"):
                return int(line.split()[2], 16)
    return None


def _ancestors(path, depth=6):
    p = Path(path)
    out = [p]
    for _ in range(depth):
        if p.parent == p:
            break
        p = p.parent
        out.append(p)
    return out


def find_port_map(exe):
    """The linker map for a running exe. The launcher runs a copy of the exe
    from mod-cache/<hash>/runtime/, so look next to it and in the folders
    above, preferring a map whose timestamp matches the exe's.
    Returns (path or None, timestamps match)."""
    candidates = [d / MAP_NAME for d in _ancestors(Path(exe).parent) if (d / MAP_NAME).exists()]
    try:
        want = pe_timestamp(exe)
    except OSError:
        want = None
    for c in candidates:
        if want is not None and map_timestamp(c) == want:
            return c, True
    return (candidates[0], False) if candidates else (None, False)


def find_xbe(exe_dir=None):
    """default.xbe: game_files/ in the current folder, else game_files/
    (or game_files/<title>/) next to the exe or in a folder above it."""
    dirs = [Path(".")] + (_ancestors(exe_dir) if exe_dir else [])
    for d in dirs:
        g = d / "game_files"
        if (g / "default.xbe").exists():
            return g / "default.xbe"
        if g.is_dir():
            hit = next(g.glob("*/default.xbe"), None)
            if hit:
                return hit
    return None


def build_tool():
    r = subprocess.run(["cmd", "/c", str(TOOL_DIR / "build.cmd")], capture_output=True, text=True)
    if r.returncode or not TOOL_EXE.exists():
        sys.exit(f"building hangsnap.exe failed:\n{r.stdout}{r.stderr}")


def run(pid, lmap, tool=None):
    """Snapshot process `pid`; returns hangsnap.exe's output text."""
    start, size, offs = lmap.block_layout()
    if "g_esp" not in offs:
        sys.exit("g_esp is not in the globals block; has the port's layout changed?")
    exe = Path(tool) if tool else TOOL_EXE
    if not exe.exists():
        if tool:
            sys.exit(f"{exe} not found")
        build_tool()
    r = subprocess.run([str(exe), str(pid), f"{start:X}", f"{size:X}", f"{offs['g_esp']:X}"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"hangsnap.exe failed ({r.returncode}): {r.stderr.strip()}")
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.stdout
