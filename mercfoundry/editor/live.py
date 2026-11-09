"""The editor's live link to a running game (the Foundry Console plugin).

The plugin polls POST /api/live/poll about four times a second with a few
text lines about the game, and gets back the commands queued since. Text
lines keep the plugin's side (C) simple:

  plugin -> editor   player <x> <y> <z> <forward x> <forward z>
                     gameplay <0|1>
                     result <text>            (one per command it ran)
                     loaded-begin / loaded <template...> / loaded-end
                                              (answering "loaded": what this area has)
  editor -> plugin   move <name|-> <model|-> <n> <x y z> * n  <x> <y> <z> <heading>
                     remove <name|-> <model|-> <n> <x y z> * n
                     teleport <x> <y> <z>
                     spawn <template> <name> <x> <y> <z> <heading>
                     loaded                   (which templates are loaded here)

An object is found by name when it has one, else by model near one of the
given positions (where the game had it, and where the editor last sent it).
Headings are degrees in the sense of the mod rule "turn". Coordinates are the
game's.
"""
import threading
import time

CONNECTED_FOR = 2.0       # seconds since the last poll that still count as connected


class Live:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = []
        self.player = None
        self.gameplay = False
        self.last_poll = 0.0
        self.results = []
        self.loaded = None          # templates loaded where the player is (after "loaded")
        self._loading = None

    # ---------------------------------------------------------------- plugin side
    def poll(self, text: str) -> str:
        with self.lock:
            self.last_poll = time.time()
            for line in text.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "player" and len(parts) >= 6:
                    try:
                        x, y, z, fx, fz = (float(v) for v in parts[1:6])
                        self.player = {"position": [x, y, z], "forward": [fx, fz]}
                    except ValueError:
                        pass
                elif parts[0] == "gameplay" and len(parts) >= 2:
                    self.gameplay = parts[1] == "1"
                elif parts[0] == "loaded-begin":
                    self._loading = []
                elif parts[0] == "loaded" and self._loading is not None:
                    self._loading += parts[1:]
                elif parts[0] == "loaded-end" and self._loading is not None:
                    self.loaded, self._loading = sorted(self._loading, key=str.lower), None
                elif parts[0] == "result":
                    self.results.append(line[len("result "):].strip())
                    self.results = self.results[-20:]
            out, self.queue = self.queue, []
        return "".join(line + "\n" for line in out)

    # ---------------------------------------------------------------- editor side
    def state(self) -> dict:
        with self.lock:
            connected = time.time() - self.last_poll < CONNECTED_FOR
            results, self.results = self.results, []
            return {"connected": connected, "gameplay": self.gameplay and connected,
                    "player": self.player if connected else None, "results": results,
                    "loaded": self.loaded if connected else None,
                    "queued": len(self.queue)}

    def send(self, commands: list) -> int:
        lines = [command_line(c) for c in commands]
        with self.lock:
            self.queue += lines
        return len(lines)


def _word(s):
    s = (s or "").strip()
    return s if s and " " not in s else "-"


def _num(v):
    return f"{float(v):.3f}"


def command_line(c: dict) -> str:
    """One command dict from the page as a plugin line."""
    op = c.get("op")
    if op in ("move", "remove"):
        pts = [p for p in c.get("from", []) if p and len(p) == 3][:4]
        head = [op, _word(c.get("name")), _word(c.get("model")), str(len(pts))]
        head += [_num(v) for p in pts for v in p]
        if op == "move":
            head += [_num(v) for v in c["position"]] + [_num(c.get("heading", 0.0))]
        return " ".join(head)
    if op == "spawn":
        t, n = _word(c.get("template")), _word(c.get("name"))
        if t == "-" or n == "-":
            raise ValueError("spawn needs a template and a name, without spaces")
        return " ".join(["spawn", t, n] + [_num(v) for v in c["position"]] + [_num(c.get("heading", 0.0))])
    if op == "loaded":
        return "loaded"
    if op == "teleport":
        return "teleport " + " ".join(_num(v) for v in c["position"])
    raise ValueError(f"unknown live command {op!r}")
