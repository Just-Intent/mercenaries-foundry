"""A Lua 5.0 checker for the scripts the game runs (they go in as source and
the game compiles them as a mission loads, so a slip anywhere loses the whole
script, with nothing on screen to say why).

check(source) -> [problems]: syntax errors, as the game's Lua 5.0 parser
sees them (no `#` or `%`: those came with 5.1), as "line N: ...".

names(source) -> (defined, called, callbacks): the global functions a
script defines, the global names it calls, and the names it hands to the
game's events as callbacks (Event_*('name', ...)), each with its line.

unknown(source, known) -> [problems]: calls and callbacks that are neither
defined in the script nor in `known` (the names the retail scripts define or
call: the game's own functions), so a misspelt function or callback shows up
before a game run.
"""

import difflib
import re

LUA_GLOBALS = {
    # Lua 5.0's base library and the tables the game has
    "assert", "collectgarbage", "dofile", "error", "gcinfo", "getfenv", "getmetatable", "ipairs", "loadfile",
    "loadlib", "loadstring", "next", "pairs", "pcall", "print", "rawequal", "rawget", "rawset", "require",
    "setfenv", "setmetatable", "tonumber", "tostring", "type", "unpack", "xpcall", "_G", "_VERSION",
    "string", "table", "math", "coroutine", "io", "os", "debug", "arg",
}

# Lua's library tables. The game's Lua is cut down: its scripts never touch
# string, math, os or io, and calling string.len there stopped a mission's
# start (0.94). So a library function counts only if the game's own scripts
# call it too (known_names records "table.getn" and the like).
LIBRARIES = {"string", "math", "os", "io", "coroutine", "debug", "table"}

KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
            "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}

# The AI commands the game reads (Lua_ArgAiCommand, 0x11F920), compared by
# the name's hash (so any letter case works), and which argument of each call
# is the command's name.
AI_COMMANDS = ("Wait", "Follow", "Operate", "TakeOff", "Attack", "TacticalMove", "EverybodyOut", "Land",
               "Patrol", "TakeCover", "MoveToActor", "Horn", "Exit", "PathMove")
AI_COMMAND_CALLS = {"Ai_Command": 1, "Ai_CommandQueue": 1, "Ai_CommandDefault": 1,
                    "Squad_Command": 2, "Squad_CommandQueue": 2, "Squad_CommandDefault": 2}
# Operate's seat (the argument after the vehicle); any other name is no seat
OPERATE_SEATS = ("driver", "gunner", "passenger", "nodriver", "nogunner", "nopassenger")
PRIORITIES = ("loPri", "medPri", "hiPri")

_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\f\v]+) | (?P<nl>\n)
  | (?P<lcomment>--\[\[) | (?P<comment>--[^\n]*)
  | (?P<lstring>\[\[)
  | (?P<number>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<string>"|')
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|[-+*/^<>=(){}\[\];:,.])
""", re.VERBOSE)


class LuaSyntaxError(ValueError):
    pass


def _long_bracket(src, i, line):
    """The end of a [[ ... ]] long string or comment starting at src[i] (Lua
    5.0's nest [[ inside), and the line it ends on."""
    depth, j = 1, i + 2
    while depth:
        a, b = src.find("[[", j), src.find("]]", j)
        if b < 0:
            raise LuaSyntaxError(f"line {line}: unfinished long string or comment")
        if 0 <= a < b:
            depth, j = depth + 1, a + 2
        else:
            depth, j = depth - 1, b + 2
    return j, line + src.count("\n", i, j)


def tokens(src):
    """[(kind, value, line)]: name, keyword, number, string, op, eof."""
    out, i, line, n = [], 0, 1, len(src)
    if src.startswith("#"):                       # a first line "#!..." is skipped, as lua.c does
        i = src.find("\n") if "\n" in src else n
    while i < n:
        m = _TOKEN.match(src, i)
        if not m:
            raise LuaSyntaxError(f"line {line}: unexpected character {src[i]!r}"
                                 + (" (the length operator # came with Lua 5.1; use table.getn)" if src[i] == "#" else "")
                                 + (" (% came with Lua 5.1; use math.mod)" if src[i] == "%" else ""))
        kind = m.lastgroup
        if kind == "nl":
            line += 1
            i = m.end()
        elif kind == "ws" or kind == "comment":
            i = m.end()
        elif kind in ("lcomment", "lstring"):
            start = m.start() + (2 if kind == "lcomment" else 0)
            end, nl = _long_bracket(src, start, line)
            if kind == "lstring":
                out.append(("string", src[start + 2:end - 2], line))
            line, i = nl, end
        elif kind == "string":
            q, j, buf = m.group(), m.end(), []
            while True:
                if j >= n or src[j] == "\n":
                    raise LuaSyntaxError(f"line {line}: unfinished string")
                c = src[j]
                if c == "\\":
                    if j + 1 < n and src[j + 1] == "\n":
                        line += 1
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if c == q:
                    break
                buf.append(c)
                j += 1
            out.append(("string", "".join(buf), line))
            i = j + 1
        elif kind == "name":
            v = m.group()
            out.append(("keyword" if v in KEYWORDS else "name", v, line))
            i = m.end()
        else:
            out.append((kind, m.group(), line))
            i = m.end()
    out.append(("eof", "<eof>", line))
    return out


_BINOPS = {"+", "-", "*", "/", "^", "..", "==", "~=", "<", "<=", ">", ">=", "and", "or"}


class _Parser:
    def __init__(self, src):
        self.t = tokens(src)
        self.i = 0
        self.scopes = [set()]
        self.defined, self.called, self.callbacks = {}, [], []
        self.commands = []      # calls that take a word from a set (AI orders, CHOICES): (callee, [each argument's string, if it is one], line)

    # -- tokens
    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def check(self, value):
        tok = self.peek()
        return tok[1] == value and tok[0] in ("op", "keyword")

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, value, what=None):
        tok = self.next()
        if tok[1] != value or tok[0] not in ("op", "keyword"):
            raise LuaSyntaxError(f"line {tok[2]}: '{value}' expected{what or ''} near '{tok[1]}'")
        return tok

    def name(self):
        tok = self.next()
        if tok[0] != "name":
            raise LuaSyntaxError(f"line {tok[2]}: name expected near '{tok[1]}'")
        return tok[1]

    # -- scopes
    def local(self, n):
        self.scopes[-1].add(n)

    def is_local(self, n):
        return any(n in s for s in self.scopes)

    # -- blocks
    def chunk(self):
        self.block()
        tok = self.peek()
        if tok[0] != "eof":
            raise LuaSyntaxError(f"line {tok[2]}: '<eof>' expected near '{tok[1]}'")

    def block_end(self):
        tok = self.peek()
        return tok[0] == "eof" or (tok[0] == "keyword" and tok[1] in ("end", "else", "elseif", "until"))

    def block(self):
        self.scopes.append(set())
        while not self.block_end():
            if self.check("return"):
                self.next()
                if not self.block_end() and not self.check(";"):
                    self.explist()
                if self.check(";"):
                    self.next()
                break
            if self.check("break"):
                self.next()
                if self.check(";"):
                    self.next()
                break
            self.statement()
            if self.check(";"):
                self.next()
        self.scopes.pop()

    def statement(self):
        tok = self.peek()
        v = tok[1] if tok[0] == "keyword" else None
        if v == "do":
            self.next(); self.block(); self.expect("end", f" (to close 'do' at line {tok[2]})")
        elif v == "while":
            self.next(); self.expr(); self.expect("do"); self.block()
            self.expect("end", f" (to close 'while' at line {tok[2]})")
        elif v == "repeat":
            self.next(); self.block(); self.expect("until", f" (to close 'repeat' at line {tok[2]})"); self.expr()
        elif v == "if":
            self.next(); self.expr(); self.expect("then"); self.block()
            while self.check("elseif"):
                self.next(); self.expr(); self.expect("then"); self.block()
            if self.check("else"):
                self.next(); self.block()
            self.expect("end", f" (to close 'if' at line {tok[2]})")
        elif v == "for":
            self.next()
            first = self.name()
            self.scopes.append({first})
            if self.check("="):
                self.next(); self.expr(); self.expect(","); self.expr()
                if self.check(","):
                    self.next(); self.expr()
            else:
                while self.check(","):
                    self.next(); self.local(self.name())
                self.expect("in", " (or '=')"); self.explist()
            self.expect("do"); self.block(); self.expect("end", f" (to close 'for' at line {tok[2]})")
            self.scopes.pop()
        elif v == "function":
            self.next()
            n = self.name()
            dotted = False
            while self.check(".") or self.check(":"):
                self.next(); self.name(); dotted = True
            if not dotted and not self.is_local(n):
                self.defined.setdefault(n, tok[2])
            self.funcbody(tok[2])
        elif v == "local":
            self.next()
            if self.check("function"):
                self.next()
                n = self.name()
                self.local(n)
                self.funcbody(tok[2])
            else:
                names = [self.name()]
                while self.check(","):
                    self.next(); names.append(self.name())
                if self.check("="):
                    self.next(); self.explist()
                for n in names:
                    self.local(n)
        else:
            self.expr_statement()

    def funcbody(self, line):
        self.expect("(")
        self.scopes.append({"arg"})
        if not self.check(")"):
            while True:
                if self.check("..."):
                    self.next()
                    break
                self.local(self.name())
                if not self.check(","):
                    break
                self.next()
        self.expect(")")
        self.block()
        self.scopes.pop()
        self.expect("end", f" (to close 'function' at line {line})")

    def expr_statement(self):
        line = self.peek()[2]
        targets = [self.suffixed()]
        if self.check("=") or self.check(","):
            while self.check(","):
                self.next(); targets.append(self.suffixed())
            for kind, n in targets:
                if kind == "call":
                    raise LuaSyntaxError(f"line {line}: cannot assign to a function call")
                if kind == "name" and not self.is_local(n):
                    self.defined.setdefault(n, line)
            self.expect("=")
            self.explist()
        elif targets[0][0] != "call":
            tok = self.peek()
            raise LuaSyntaxError(f"line {tok[2]}: syntax error near '{tok[1]}' (a statement is a call or an assignment)")

    # -- expressions
    def explist(self):
        self.expr()
        while self.check(","):
            self.next(); self.expr()

    def expr(self):
        while self.check("not") or self.check("-"):
            self.next()
        self.simple()
        while True:
            tok = self.peek()
            if tok[1] in _BINOPS and tok[0] in ("op", "keyword"):
                self.next()
                while self.check("not") or self.check("-"):
                    self.next()
                self.simple()
            else:
                break

    def simple(self):
        tok = self.peek()
        if tok[0] in ("number", "string") or (tok[0] == "keyword" and tok[1] in ("nil", "true", "false")) \
                or (tok[0] == "op" and tok[1] == "..."):
            self.next()
        elif self.check("function"):
            self.next(); self.funcbody(tok[2])
        elif self.check("{"):
            self.table()
        else:
            self.suffixed()

    def table(self):
        line = self.expect("{")[2]
        while not self.check("}"):
            if self.check("["):
                self.next(); self.expr(); self.expect("]"); self.expect("="); self.expr()
            elif self.peek()[0] == "name" and self.peek(1)[1] == "=" and self.peek(1)[0] == "op":
                self.next(); self.next(); self.expr()
            else:
                self.expr()
            if self.check(",") or self.check(";"):
                self.next()
            elif not self.check("}"):
                tok = self.peek()
                raise LuaSyntaxError(f"line {tok[2]}: '}}' expected (to close '{{' at line {line}) near '{tok[1]}'")
        self.next()

    def suffixed(self):
        """('name', n) for a plain name, ('call', n) for a call, ('index', None)."""
        tok = self.next()
        if tok[0] == "name":
            kind, base = "name", tok[1]
        elif tok[1] == "(" and tok[0] == "op":
            self.expr(); self.expect(")")
            kind, base = "paren", None
        else:
            raise LuaSyntaxError(f"line {tok[2]}: unexpected symbol near '{tok[1]}'")
        lib = None          # "string.len": a field of a standard library table, called
        while True:
            t = self.peek()
            if t[0] == "op" and t[1] == ".":
                self.next()
                field = self.name()
                lib = f"{base}.{field}" if kind == "name" and base in LIBRARIES and not self.is_local(base) else None
                kind, base = "index", None
                continue
            if lib and ((t[0] == "op" and t[1] in ("(", "{")) or t[0] == "string"):
                self.called.append((lib, t[2]))
            lib = None
            if t[0] == "op" and t[1] == "[":
                self.next(); self.expr(); self.expect("]"); kind, base = "index", None
            elif t[0] == "op" and t[1] == ":":
                self.next(); self.name(); self.args(None, t[2]); kind, base = "call", None
            elif (t[0] == "op" and t[1] in ("(", "{")) or t[0] == "string":
                if t[0] == "op" and t[1] == "(" and t[2] != self.t[self.i - 1][2]:
                    # Lua 5.0 refuses a call's '(' on a new line ("ambiguous syntax")
                    raise LuaSyntaxError(f"line {t[2]}: ambiguous syntax (function call x new statement)")
                callee = base if kind == "name" and not self.is_local(base) else None
                if callee:
                    self.called.append((callee, t[2]))
                self.args(callee, t[2])
                kind, base = "call", None
            else:
                return kind, base

    def args(self, callee, line):
        tok = self.peek()
        if tok[0] == "string":
            self.next()
            return
        if self.check("{"):
            self.table()
            return
        self.expect("(")
        first = True
        literals = [] if callee in AI_COMMAND_CALLS or callee in CHOICES else None
        if not self.check(")"):
            while True:
                t = self.peek()
                if literals is not None:
                    alone = t[0] == "string" and self.peek(1)[1] in (",", ")")
                    literals.append(t[1] if alone else None)
                # Event_*('callback', ...): the game calls that global by name
                if first and callee and callee.startswith("Event_") and t[0] == "string" \
                        and re.fullmatch(r"[A-Za-z_]\w*", t[1]) and self.peek(1)[1] in (",", ")"):
                    self.callbacks.append((t[1], t[2]))
                first = False
                self.expr()
                if not self.check(","):
                    break
                self.next()
        self.expect(")")
        if literals is not None:
            self.commands.append((callee, literals, line))


def check(source):
    """Syntax problems in a Lua 5.0 script (an empty list: it parses)."""
    try:
        _Parser(source).chunk()
    except LuaSyntaxError as e:
        return [str(e)]
    return []


def names(source):
    """(defined {name: line}, called [(name, line)], callbacks [(name, line)])."""
    p = _Parser(source)
    p.chunk()
    return p.defined, p.called, p.callbacks


# Script commands Foundry Console adds (docs/OVERLAY.md): generated scripts
# call them only after checking they exist (`if Foundry_MapText then`).
PLUGIN_COMMANDS = frozenset({"Foundry_TrafficZone", "Foundry_SetHandling", "Foundry_Range", "Foundry_Sample",
                             "Foundry_Label", "Foundry_MapText", "Foundry_MapTexture", "Foundry_HqMode",
                             "Foundry_MapTemplate", "Foundry_Log", "Foundry_ScriptValues", "Foundry_CentreWheels", "Foundry_MapCaptives", "Foundry_MapFines", "Foundry_ShopUrl", "Foundry_FindPlaced", "Foundry_MoveObject", "Foundry_RemoveObject",
                             "Foundry_ActorTemplate", "Foundry_SpawnTemplate", "Foundry_MapShop", "Foundry_MapShopOpen", "Foundry_ShopUnlocked", "Foundry_ShopRestore", "Foundry_FlyLoop", "Foundry_PhysicsProbe", "Foundry_ProjectilePos", "Foundry_AiAlert", "Foundry_IsEngaging", "Foundry_ActorModel"})


def known_names(sources):
    """Every global a set of scripts defines or calls (the retail scripts: the
    game's own functions and the engine's), and the library functions they
    call ("table.getn"): the Lua the game is known to have."""
    out = set()
    for s in sources:
        try:
            d, c, cb = names(s)
        except LuaSyntaxError:
            continue
        out |= set(d) | {n for n, _ in c} | {n for n, _ in cb}
    return out


def unknown(source, known):
    """Calls and event callbacks that nothing defines: not this script, not
    `known` (known_names of the retail scripts). Lua's own functions count only
    if the game's scripts call them too: its Lua is cut down (no string
    library, for one)."""
    defined, called, callbacks = names(source)
    have = set(defined) | set(known)
    out, seen = [], set()
    for n, line in called:
        if n not in have and n not in seen:
            seen.add(n)
            if "." in n or n in LUA_GLOBALS:
                out.append(f"line {line}: calls {n}, which the game's scripts never use (its Lua may not have it)")
            else:
                out.append(f"line {line}: calls {n}, which nothing defines (not this script, not the game's)")
    for n, line in callbacks:
        if n not in set(defined) and n not in known and n not in seen:
            seen.add(n)
            out.append(f"line {line}: an event calls back {n}, which nothing defines")
    return out


def ai_commands(source):
    """AI commands the game won't know. For a command name it doesn't have,
    Ai_Command('guy', 'Atack', ...) and the Squad_ forms build an empty command
    (the actor carries on as before); Operate takes an unknown seat as none
    given. Only names written out as strings are checked."""
    from .hashing import name_hash
    commands = {name_hash(c): c for c in AI_COMMANDS}
    seats = {name_hash(s) for s in OPERATE_SEATS + PRIORITIES}
    p = _Parser(source)
    p.chunk()
    out = []
    for callee, args, line in p.commands:
        if callee not in AI_COMMAND_CALLS:
            continue
        at = AI_COMMAND_CALLS[callee]
        name = args[at] if at < len(args) else None
        if name is None:
            continue
        known = commands.get(name_hash(name))
        seat = args[at + 2] if at + 2 < len(args) else None
        if not known:
            out.append(f"line {line}: {callee} gives the command {name!r}, which the game doesn't have "
                       f"(it has {', '.join(AI_COMMANDS)})")
        elif known == "Operate" and seat is not None and name_hash(seat) not in seats:
            out.append(f"line {line}: Operate's seat {seat!r} isn't one the game has "
                       f"({', '.join(OPERATE_SEATS)}), so it's taken as no seat given")
    return out


# Other commands that read one of a fixed set of words, compared by the word's
# hash as the game does (the retail code: map/manual.json and FUNCTION_MAP.md).
# Each: (argument index, words, extra hashes the game also takes but no retail
# script names, what an unknown word does).
CHOICES = {
    "Objective_SetTargetActor": (3, ("TARGET_TYPE_OBJECTIVE_PRIMARY", "TARGET_TYPE_OBJECTIVE_SECONDARY",
                                     "TARGET_TYPE_HQ_ALLIES", "TARGET_TYPE_HQ_CHINA", "TARGET_TYPE_HQ_SK",
                                     "TARGET_TYPE_HQ_NK", "TARGET_TYPE_HQ_MAFIA", "TARGET_TYPE_UNIT_SAMS",
                                     "TARGET_TYPE_UNDEFINED"),
                                 (0x2B358195, 0x65C601D2, 0x58597A6B, 0x1B3A74BB),
                                 "the target shows as undefined"),
    "Effect_Explosion": (3, ("small", "medium", "large"), (), "there's no explosion"),
    "Building_SpawnerSetAlertState": (1, ("danger", "caution", "normal"), (), "the spawner goes to its normal state"),
    "Utility_GetActorsInRange": (0, ("TYPE_VEHICLE_HUMAN_GENERAL", "TYPE_VEHICLE_CAR_GENERAL",
                                     "TYPE_VEHICLE_TANK_GENERAL", "TYPE_VEHICLE_HELICOPTER_GENERAL",
                                     "TYPE_PROP_CAR"), (), "it finds nothing"),
    "Event_OdfToActorProximity": (3, ("TYPE_VEHICLE_HUMAN_GENERAL", "TYPE_VEHICLE_CAR_GENERAL",
                                      "TYPE_VEHICLE_TANK_GENERAL", "TYPE_VEHICLE_HELICOPTER_GENERAL",
                                      "TYPE_ITEM_WEAPON"), (), "it watches for TYPE_UNDEFINED objects"),
}
# What Actor_GetType returns: every object kind's name (ObjectKind_Name,
# 0x11640), from the objectType property (WorldObject_GetKind, 0x117B0)
OBJECT_KINDS = tuple("TYPE_" + k for k in (
    "BUILDING", "BUILDING_ALARM", "BUILDING_ENTRANCE", "BUILDING_PRISON", "BUILDING_SPAWNER", "BUILDING_CHOPSHOP",
    "BUILDING_HUMANTURRETS", "BUILDING_MONUMENT", "BUILDING_GATE", "BUILDING_SILO", "BUILDING_CRANE", "ITEM_WEAPON",
    "ITEM_BOUNTY", "ITEM_CACHE", "ITEM_PROP", "LIGHT", "LOCATION", "PICKUP", "PROP", "PROP_CRATE", "LISTENING_POST",
    "PROP_CAR", "STATIC", "TREE", "VEHICLE_AIRPLANE_GENERAL", "VEHICLE_CAR_GENERAL", "VEHICLE_EMPLACED",
    "VEHICLE_HELICOPTER_GENERAL", "VEHICLE_HUMAN_GENERAL", "VEHICLE_HUMAN_PLAYER", "VEHICLE_TANK_GENERAL", "WATER",
    "UNDEFINED"))
CHOICES["Objective_SetTargetVector"] =CHOICES["Objective_SetTargetActor"]
CHOICES["Event_OdfToVectorProximity"] = CHOICES["Event_OdfToActorProximity"]


def choices(source):
    """Words the game won't know where a command takes one of a fixed set:
    AI orders (ai_commands) and the commands in CHOICES. Only words written
    out as strings are checked."""
    from .hashing import name_hash
    p = _Parser(source)
    p.chunk()
    out = ai_commands(source)
    for callee, args, line in p.commands:
        if callee not in CHOICES:
            continue
        at, words, extra, effect = CHOICES[callee]
        word = args[at] if at < len(args) else None
        if word is not None and name_hash(word) not in {name_hash(w) for w in words} | set(extra):
            out.append(f"line {line}: {callee} takes {word!r}, which the game doesn't know "
                       f"({', '.join(words)}), so {effect}")
    # x == 'TYPE_...': Actor_GetType gives one of OBJECT_KINDS, exactly as written
    t = p.t
    for i, (kind, value, line) in enumerate(t):
        if kind == "string" and value.startswith("TYPE_") and not value.startswith("TYPE_BOUNDARY_") \
                and value not in OBJECT_KINDS and (t[i - 1][1] in ("==", "~=") or t[i + 1][1] in ("==", "~=")):
            near = difflib.get_close_matches(value, OBJECT_KINDS, 1, 0.86)
            out.append(f"line {line}: compares with {value!r}, a type Actor_GetType never gives, so it's never "
                       f"equal" + (f" (the nearest it gives: {near[0]})" if near else ""))
    return sorted(out, key=lambda s: int(s.split()[1].rstrip(":")))
