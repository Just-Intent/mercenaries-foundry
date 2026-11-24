"""A stand-in for the game, to run a build's scripts through whole sequences
(a pilot's trip to a new map and back, a save there and its reload) and look
at what ends up where: the faction moods, the PDA's card keys, the script
values. Uses luarun's Lua 5.0 interpreter on the built archives' scripts.

What it models (from the retail code and the game's own logs; see each):
- script values (scribble memory): text, numbers written as "%f";
- faction relations: kept in memory across level loads (the 0.302 logs: SW's
  moods were still there when Gaza's ScriptInit ran);
- player data: Utility_SavePlayerData copies the relations to the player
  into script values, Utility_LoadPlayerData and a save's load from the menu
  (0x189490 from the front end) read them back;
- a level load: a fresh Lua state and no timers; SkipTo_Map becomes the
  level's name; a game map (sw, nw) runs what global's ScriptInit does that
  matters here (an intel meter for every suit, Utility_LoadPlayerData, then
  the foundry_layers hook in LoadNoMissionState); a new map runs its master
  script's ScriptInit;
- a save made in game: Utility_SavePlayerData, then the script values;
  loading it: the values back, the player data read, and the level the game
  resumes (SW: retail's resume knows only its own maps);
- timers (Event_RelativeTimer); Utility_ResetToBaseLayerOnly loads that map
  once the running call is over; the screen's fade.
Everything else the scripts call is a stub (counted in .stubbed).
"""

import copy

from .luarun import Interpreter, LuaTable, LuaError, tostr
from .dsk import Archive
from .hashing import name_hash

GAME_MAPS = ("sw", "nw")
SUITS = ("clubs", "diamonds", "hearts", "spades")
MOOD_FACTIONS = ("allies", "china", "mafia", "sk")


def built_scripts(*folders):
    """{name hash: source} of every script in these build or game folders'
    assets.dsk (the first folder's win)."""
    from . import script
    out = {}
    for folder in folders:
        a = Archive(f"{folder}/assets.dsk")
        for r in a.records:
            if r.key in out:
                continue
            try:
                out[r.key] = script.decode(a.read(r)).source
            except Exception:
                continue
    return out


class Game:
    def __init__(self, scripts, start_relations=None):
        self.scripts = scripts
        self.scribble = {}
        self.relations = dict(start_relations or {})
        self.log = []               # Foundry_Log lines and the sim's own notes
        self.level = None
        self.time = 0.0
        self.black = False
        self.pending = None
        self.lua = None
        self.timers = []
        self.stubbed = {}
        self.errors = []
        self.shop = set()           # the PDA shop's unlocked list
        self.popups = []            # "new support item" notices, in order
        self.pda = {}               # the card keys the PDA was built from

    # ------------------------------------------------------------ the game's functions
    def _install(self, lua):
        g = lua.globals
        S = self.scribble

        def read_s(k, *_):
            v = S.get(k)
            return (v,)

        def read_n(k, *_):
            v = S.get(k)
            if v is None:
                return (None,)
            try:
                return (float(v),)
            except ValueError:
                return (0.0,)

        def write_s(k, v=None, *_):
            S[k] = tostr(v)
            return ()

        def write_n(k, v=None, *_):
            S[k] = "%f" % float(v or 0)
            return ()

        def erase(k, *_):
            S.pop(k, None)
            return ()

        def get_rel(a, b, *_):
            return (float(self.relations.get((a, b), 0.0)),)

        def set_rel(a, b, v, *_):
            self.relations[(a, b)] = float(v)
            return ()

        def load_util(name, *_):
            self.run_script(name)
            return ()

        def timer(name, mode=None, secs=0, *_):
            self.timers.append((self.time + float(secs or 0), len(self.timers), name))
            return (float(len(self.timers)),)

        def reset(m, *_):
            self.pending = str(m).lower()
            return ()

        def fade_out(*_):
            self.black = True
            return ()

        def fade_in(*_):
            self.black = False
            return ()

        def flog(s=None, *_):
            self.log.append(tostr(s))
            return ()

        g.update({
            "Utility_ReadStringFromScribbleMemory": read_s, "Utility_ReadNumberFromScribbleMemory": read_n,
            "Utility_WriteStringToScribbleMemory": write_s, "Utility_WriteNumberToScribbleMemory": write_n,
            "Utility_EraseValueFromScribbleMemory": erase,
            "Faction_GetRelation": get_rel, "Faction_SetRelation": set_rel,
            "Utility_LoadUtilityScript": load_util, "Event_RelativeTimer": timer,
            "Utility_ResetToBaseLayerOnly": reset, "Cinematic_FadeOut": fade_out, "Cinematic_FadeIn": fade_in,
            "Utility_SavePlayerData": lambda *a: (self.save_player_data(), ())[1],
            "Utility_LoadPlayerData": lambda *a: (self.load_player_data(), ())[1],
            "Foundry_Log": flog,
            "Utility_DoesActorExist": lambda *a: (False,),
            # the PDA shop: Shop_UnlockItem queues the "new support item"
            # popup (0xCDA60 with 0), Foundry_ShopRestore doesn't (with 1)
            "Shop_UnlockItem": lambda t, *a: (self._unlock(t, True), ())[1],
            "Foundry_ShopRestore": lambda t, *a: (self._unlock(t, False), (True,))[1],
            "Foundry_ShopUnlocked": lambda t, *a: (str(t).lower() in self.shop,),
            # numbers the scripts do sums with (the low end of a random range)
            "Utility_GetRandomFloat": lambda lo=0.0, hi=1.0, *a: (float(lo or 0),),
            "Utility_GetRandomInt": lambda lo=0.0, hi=1.0, *a: (float(lo or 0),),
            "Player_GetMoney": lambda *a: (0.0,),
            "Actor_GetPosition": lambda *a: (0.0, 0.0, 0.0),
            "Actor_GetYaw": lambda *a: (0.0,),
            "Location_GetPosition": lambda *a: (0.0, 0.0, 0.0),
            "Utility_GetStringById": lambda *a: ("",),
            "Utility_MathFloor": lambda v=0.0, *a: (float(int(v or 0) // 1),),
        })

    def _unlock(self, t, popup):
        t = str(t).lower()
        if t not in self.shop:
            self.shop.add(t)
            if popup:
                self.popups.append(t)

    def save_player_data(self):
        self.scribble["__playerdata_shop"] = " ".join(sorted(self.shop))
        for f in MOOD_FACTIONS:
            self.scribble[f"__playerdata_{f}"] = "%f" % self.relations.get((f, "prokat"), 0.0)

    def load_player_data(self):
        shop = self.scribble.get("__playerdata_shop")
        if shop is not None:
            self.shop = set(shop.split())
        for f in MOOD_FACTIONS:
            v = self.scribble.get(f"__playerdata_{f}")
            if v is not None:
                self.relations[(f, "prokat")] = float(v)

    # ------------------------------------------------------------ scripts and levels
    def run_script(self, name):
        src = self.scripts.get(name_hash(name))
        if src is None:
            self.log.append(f"[sim] no script {name}")
            return
        self.lua.run(src, name)

    def _call(self, fn, *args):
        try:
            if self.lua.globals.get(fn) is None:
                self.log.append(f"[sim] no function {fn}")
                return
            self.lua.call(fn, *args)
        except LuaError as e:
            self.errors.append(f"{self.level}: {fn}: {e}")
            self.log.append(f"[sim] Lua error in {fn}: {e}")

    def load_level(self, m):
        """Load a level, then whatever level changes it asks for."""
        while True:
            self._start_level(m)
            if not self.pending:
                return
            m, self.pending = self.pending, None

    def _start_level(self, m):
        m = m.lower()
        self.level = m
        self.log.append(f"[sim] level {m}")
        self.timers = []
        self.shop = set()           # a level load empties the list (the save's restore, or the
                                    # level's own unlocks, fill it again)
        self.lua = Interpreter()
        self._install(self.lua)
        self.scribble["SkipTo_Map"] = m
        # Level_Load builds the PDA's cards and meters from the script values
        # (0xCE460) before any script runs: what it saw, for pda_stale()
        self.pda = {k: v for k, v in self.scribble.items() if self._pda_key(k)}
        if m in GAME_MAPS:
            for suit in SUITS:                          # gameflow_GenericInit
                self.scribble.setdefault("iCurrentIntel" + suit, "%f" % 0)
            self.load_player_data()                     # global's ScriptInit, before LoadNoMissionState
            if name_hash("foundry_layers") in self.scripts:
                try:
                    self.run_script("foundry_layers")   # the hook in LoadNoMissionState
                except LuaError as e:
                    self.errors.append(f"{m}: foundry_layers: {e}")
                self._call("foundry_LoadMapLayers")
        else:
            try:
                self.run_script(m)
            except LuaError as e:
                self.errors.append(f"{m}: master script: {e}")
            self._call("ScriptInit")
        for k, v in self.lua.stubbed.items():
            self.stubbed[k] = self.stubbed.get(k, 0) + v

    def advance(self, seconds):
        """Run the timers that come due; a level change they ask for happens
        at once (and the rest of the time runs on the new level)."""
        end = self.time + seconds
        while True:
            due = sorted(t for t in self.timers if t[0] <= end)
            if not due:
                break
            t = due[0]
            self.timers.remove(t)
            self.time = t[0]
            self._call(t[2])
            if self.pending:
                m, self.pending = self.pending, None
                self.load_level(m)
        self.time = end

    # ------------------------------------------------------------ what the player does
    def travel(self, to):
        """Say yes to the pilot that goes to `to` on this level."""
        prefix = "foundry_Travel" if self.level in GAME_MAPS else f"foundry_{self.level}_Travel"
        lst = self.lua.globals.get(prefix + "List")
        if not isinstance(lst, LuaTable):
            raise AssertionError(f"no travel pilots on {self.level}")
        for i in range(1, lst.getn() + 1):
            t = lst.get(i)
            if isinstance(t, LuaTable) and str(t.get("to")).lower() == to.lower():
                self._call(f"{prefix}Go{i}")
                break
        else:
            raise AssertionError(f"no pilot on {self.level} goes to {to}")
        if self.pending:
            m, self.pending = self.pending, None
            self.load_level(m)

    def save(self):
        """The pause menu's save: the player data, then the script values."""
        self.save_player_data()
        return copy.deepcopy(self.scribble)

    def load_save(self, saved):
        """The menu's load: the values back, the player data read, and the
        level the game resumes (its own map's: SW)."""
        self.scribble.clear()
        self.scribble.update(copy.deepcopy(saved))
        self.load_player_data()
        self.load_level("sw")

    # ------------------------------------------------------------ looking
    @staticmethod
    def _pda_key(k):
        low = k.lower()
        return (low.startswith("icurrentintel") or low.startswith("displayedintel")
                or any(low.startswith(s) and (low.endswith("status") or low.endswith("statusdisplayed"))
                       for s in SUITS))

    def pda_stale(self):
        """The card keys the PDA was built from at this level's load that
        differ from the live ones now (empty: it shows this map's)."""
        live = {k: v for k, v in self.scribble.items() if self._pda_key(k)}

        def value(d, k):
            v = d.get(k)
            if k.lower().endswith("status"):
                return v
            try:                    # a number: none reads as 0 to the PDA
                return float(v) if v is not None else 0.0
            except ValueError:
                return v
        keys = set(live) | set(self.pda)
        return sorted(k for k in keys if value(live, k) != value(self.pda, k))

    def moods(self):
        return {f: self.relations.get((f, "prokat"), 0.0) for f in MOOD_FACTIONS}

    def intel(self):
        return {s: float(self.scribble.get("iCurrentIntel" + s, "0")) for s in SUITS}

    def card(self, suit, rank):
        return self.scribble.get(f"{suit}{rank}status")


def round_trip(scripts, new_map, sw_moods=None):
    """The sequence that kept going wrong in game, on a build's scripts:
    start on SW with set moods and cards, a pilot to the new map, change its
    moods and cards, save there, the pilot back, load that save (it wakes on
    SW and goes back), save and load it again, the pilot back to SW. Returns
    (problems, report lines): each step checks the level, the moods to the
    player, the four intel meters and that the screen isn't left black."""
    g = Game(scripts)
    sw_moods = dict(sw_moods or {"allies": 0.36, "china": 0.26, "mafia": -0.05, "sk": 0.51})
    for f, v in sw_moods.items():
        g.relations[(f, "prokat")] = v
    g.scribble.update({"clubs5status": "killed", "hearts3status": "captured",
                       "iCurrentIntelclubs": "%f" % 5, "iCurrentIntelhearts": "%f" % 3})
    sw_intel = {"clubs": 5.0, "diamonds": 0.0, "hearts": 3.0, "spades": 0.0}
    problems, report = [], []
    state = {}

    def step(tag, level, moods=None, intel=None):
        m = {k: round(v, 2) for k, v in g.moods().items()}
        i = g.intel()
        bad = []
        if g.level != level:
            bad.append(f"on {g.level}, not {level}")
        if moods is not None and m != moods:
            bad.append(f"moods {m}, not {moods}")
        if intel is not None and i != intel:
            bad.append(f"intel {i}, not {intel}")
        if g.black:
            bad.append("the screen is left black")
        stale = g.pda_stale()
        if stale:
            bad.append(f"the PDA was built from the other map's cards ({', '.join(stale[:4])})")
        report.append(f"{'ok ' if not bad else 'BAD'} {tag}: {g.level}, moods {m}, intel {i}")
        problems.extend(f"{tag}: {b}" for b in bad)
        return m, i

    g.load_level("sw"); g.advance(5)
    step("start on SW", "sw", sw_moods, sw_intel)
    g.travel(new_map); g.advance(5)
    decks = g.lua.globals.get("tFoundryDecks")
    has_deck = isinstance(decks, LuaTable) and decks.get(new_map) is not None
    # a map with its own deck shows every suit empty on the first visit
    state["moods"], state["intel"] = step(f"pilot to {new_map}", new_map, None,
                                          {s: 0.0 for s in SUITS} if has_deck else sw_intel)
    if state["moods"] == {k: round(v, 2) for k, v in sw_moods.items()}:
        report.append(f"    ({new_map} shows SW's moods: right only if the map has no \"own_moods\")")
    # play there: a mood and a card change
    g.relations[("china", "prokat")] = -0.8
    g.scribble["spades7status"] = "killed"
    g.scribble["iCurrentIntelspades"] = "%f" % 7
    there_moods, there_intel = {k: round(v, 2) for k, v in g.moods().items()}, g.intel()
    saved = g.save()
    g.travel("sw"); g.advance(5)
    step("pilot back to SW", "sw", None if state["moods"] == {k: round(v, 2) for k, v in sw_moods.items()} else sw_moods,
         sw_intel)
    g.load_save(saved); g.advance(10)
    step(f"the save made on {new_map}, loaded", new_map, there_moods, there_intel)
    g.load_save(g.save()); g.advance(10)
    step("saved and loaded again", new_map, there_moods, there_intel)
    g.travel("sw"); g.advance(5)
    step("pilot back to SW", "sw", None, sw_intel)
    twice = sorted({t for t in g.popups if g.popups.count(t) > 1})
    if twice:
        problems.append(f"the \"new support item\" popup came again for {', '.join(twice)}")
    report.append(f"    store popups: {', '.join(g.popups) or 'none'}")
    problems += [f"Lua error: {e}" for e in g.errors]
    return problems, report
