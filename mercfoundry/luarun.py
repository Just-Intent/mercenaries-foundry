"""A small Lua 5.0 interpreter, for running the scripts a build generates
against a stand-in for the game (gamesim.py), so whole sequences (a trip to
a new map, a save there and its reload) can be tried without a game run.

It follows the game's Lua: 5.0 syntax (luacheck's tokens), `for k, v in t do`
over a table, `arg` for `...`, numbers as floats, nil and false as the only
false values. Globals no script defines and the host doesn't give are
callable stubs that return nothing and are counted (Interpreter.stubbed), so
a game function the sim doesn't model shows up instead of stopping the run.

    lua = Interpreter()
    lua.globals["Foo"] = lambda *args: (1,)     # host functions return a tuple (or None)
    lua.run(source, "name")                      # runs a chunk
    lua.call("ScriptInit")                       # calls a global function
"""

from .luacheck import tokens, LuaSyntaxError


class LuaError(RuntimeError):
    pass


class LuaTable:
    __slots__ = ("d",)

    def __init__(self, d=None):
        self.d = d if d is not None else {}

    def get(self, k):
        if isinstance(k, float) and k.is_integer():
            k = int(k)
        return self.d.get(k)

    def set(self, k, v):
        if k is None:
            raise LuaError("table index is nil")
        if isinstance(k, float) and k.is_integer():
            k = int(k)
        if v is None:
            self.d.pop(k, None)
        else:
            self.d[k] = v

    def getn(self):
        n = 0
        while (n + 1) in self.d:
            n += 1
        return n

    def __repr__(self):
        return f"LuaTable({self.d!r})"


class LuaFunction:
    __slots__ = ("params", "vararg", "body", "env", "name", "interp", "chunk")

    def __init__(self, params, vararg, body, env, name, interp, chunk="?"):
        self.params, self.vararg, self.body, self.env, self.name, self.interp = params, vararg, body, env, name, interp
        self.chunk = chunk

    def __call__(self, *args):
        return self.interp.call_function(self, list(args))


class _Return(Exception):
    def __init__(self, values):
        self.values = values


class _Break(Exception):
    pass


def tostr(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        f = float(v)
        if f.is_integer() and abs(f) < 1e15:
            return str(int(f))
        return "%.14g" % f
    if isinstance(v, str):
        return v
    if isinstance(v, LuaTable):
        return "table: %08x" % (id(v) & 0xFFFFFFFF)
    return "function: %08x" % (id(v) & 0xFFFFFFFF)


def truthy(v):
    return v is not None and v is not False


def _num(v, line):
    if isinstance(v, bool):
        raise LuaError(f"line {line}: attempt to perform arithmetic on a boolean value")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            pass
    raise LuaError(f"line {line}: attempt to perform arithmetic on a {_type(v)} value")


def _type(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, LuaTable):
        return "table"
    return "function"


def _unescape(s):
    out, i = [], 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
              "\\": "\\", '"': '"', "'": "'", "\n": "\n", "[": "[", "]": "]"}
    while i < len(s):
        c = s[i]
        if c != "\\" or i + 1 >= len(s):
            out.append(c)
            i += 1
            continue
        e = s[i + 1]
        if e.isdigit():
            j = i + 1
            while j < len(s) and j < i + 4 and s[j].isdigit():
                j += 1
            out.append(chr(int(s[i + 1:j])))
            i = j
        else:
            out.append(simple.get(e, e))
            i += 2
    return "".join(out)


# ---------------------------------------------------------------- parser
# AST nodes are tuples: (kind, line, ...)

_PRI = {"or": (1, 1), "and": (2, 2), "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3),
        "==": (3, 3), "..": (5, 4), "+": (6, 6), "-": (6, 6), "*": (7, 7), "/": (7, 7), "^": (10, 9)}
_UNARY_PRI = 8


class _P:
    def __init__(self, src):
        self.t = tokens(src)
        self.i = 0

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def is_(self, v, k=0):
        tok = self.peek(k)
        return tok[1] == v and tok[0] in ("op", "keyword")

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, v):
        tok = self.next()
        if tok[1] != v or tok[0] not in ("op", "keyword"):
            raise LuaSyntaxError(f"line {tok[2]}: '{v}' expected near '{tok[1]}'")
        return tok

    def name(self):
        tok = self.next()
        if tok[0] != "name":
            raise LuaSyntaxError(f"line {tok[2]}: name expected near '{tok[1]}'")
        return tok[1]

    def block_end(self):
        tok = self.peek()
        return tok[0] == "eof" or (tok[0] == "keyword" and tok[1] in ("end", "else", "elseif", "until"))

    def chunk(self):
        b = self.block()
        if self.peek()[0] != "eof":
            tok = self.peek()
            raise LuaSyntaxError(f"line {tok[2]}: '<eof>' expected near '{tok[1]}'")
        return b

    def block(self):
        stats = []
        while not self.block_end():
            if self.is_("return"):
                line = self.next()[2]
                exprs = [] if (self.block_end() or self.is_(";")) else self.explist()
                if self.is_(";"):
                    self.next()
                stats.append(("return", line, exprs))
                break
            if self.is_("break"):
                line = self.next()[2]
                if self.is_(";"):
                    self.next()
                stats.append(("break", line))
                break
            stats.append(self.statement())
            if self.is_(";"):
                self.next()
        return stats

    def statement(self):
        tok = self.peek()
        line = tok[2]
        v = tok[1] if tok[0] == "keyword" else None
        if v == "do":
            self.next(); b = self.block(); self.expect("end")
            return ("do", line, b)
        if v == "while":
            self.next(); c = self.expr(); self.expect("do"); b = self.block(); self.expect("end")
            return ("while", line, c, b)
        if v == "repeat":
            self.next(); b = self.block(); self.expect("until"); c = self.expr()
            return ("repeat", line, b, c)
        if v == "if":
            self.next()
            arms = []
            c = self.expr(); self.expect("then"); arms.append((c, self.block()))
            orelse = None
            while self.is_("elseif"):
                self.next(); c = self.expr(); self.expect("then"); arms.append((c, self.block()))
            if self.is_("else"):
                self.next(); orelse = self.block()
            self.expect("end")
            return ("if", line, arms, orelse)
        if v == "for":
            self.next()
            first = self.name()
            if self.is_("="):
                self.next()
                a = self.expr(); self.expect(","); b = self.expr()
                c = None
                if self.is_(","):
                    self.next(); c = self.expr()
                self.expect("do"); body = self.block(); self.expect("end")
                return ("fornum", line, first, a, b, c, body)
            names = [first]
            while self.is_(","):
                self.next(); names.append(self.name())
            self.expect("in")
            exprs = self.explist()
            self.expect("do"); body = self.block(); self.expect("end")
            return ("forin", line, names, exprs, body)
        if v == "function":
            self.next()
            path = [self.name()]
            method = False
            while self.is_("."):
                self.next(); path.append(self.name())
            if self.is_(":"):
                self.next(); path.append(self.name()); method = True
            f = self.funcbody(line, ".".join(path), method)
            return ("funcstat", line, path, f)
        if v == "local":
            self.next()
            if self.is_("function"):
                self.next()
                n = self.name()
                return ("localfunc", line, n, self.funcbody(line, n, False))
            names = [self.name()]
            while self.is_(","):
                self.next(); names.append(self.name())
            exprs = []
            if self.is_("="):
                self.next(); exprs = self.explist()
            return ("local", line, names, exprs)
        e = self.suffixed()
        if self.is_("=") or self.is_(","):
            targets = [e]
            while self.is_(","):
                self.next(); targets.append(self.suffixed())
            self.expect("=")
            for t in targets:
                if t[0] not in ("name", "index"):
                    raise LuaSyntaxError(f"line {line}: cannot assign to that")
            return ("assign", line, targets, self.explist())
        if e[0] not in ("call", "method"):
            raise LuaSyntaxError(f"line {line}: syntax error (a statement is a call or an assignment)")
        return ("callstat", line, e)

    def funcbody(self, line, name, method):
        self.expect("(")
        params, vararg = (["self"] if method else []), False
        if not self.is_(")"):
            while True:
                if self.is_("..."):
                    self.next(); vararg = True
                    break
                params.append(self.name())
                if not self.is_(","):
                    break
                self.next()
        self.expect(")")
        body = self.block()
        self.expect("end")
        return ("function", line, params, vararg, body, name)

    def explist(self):
        out = [self.expr()]
        while self.is_(","):
            self.next(); out.append(self.expr())
        return out

    def expr(self, limit=0):
        tok = self.peek()
        if (tok[0] == "keyword" and tok[1] == "not") or (tok[0] == "op" and tok[1] == "-"):
            self.next()
            operand = self.expr(_UNARY_PRI)
            left = ("not" if tok[1] == "not" else "neg", tok[2], operand)
        else:
            left = self.simple()
        while True:
            tok = self.peek()
            op = tok[1] if tok[0] in ("op", "keyword") else None
            if op not in _PRI or _PRI[op][0] <= limit:
                break
            self.next()
            right = self.expr(_PRI[op][1])
            left = ("binop", tok[2], op, left, right)
        return left

    def simple(self):
        tok = self.peek()
        if tok[0] == "number":
            self.next()
            v = tok[1]
            return ("const", tok[2], float(int(v, 16)) if v.lower().startswith("0x") else float(v))
        if tok[0] == "string":
            self.next()
            return ("const", tok[2], _unescape(tok[1]))
        if tok[0] == "keyword" and tok[1] in ("nil", "true", "false"):
            self.next()
            return ("const", tok[2], {"nil": None, "true": True, "false": False}[tok[1]])
        if tok[0] == "op" and tok[1] == "...":
            self.next()
            return ("vararg", tok[2])
        if tok[0] == "keyword" and tok[1] == "function":
            self.next()
            return self.funcbody(tok[2], "anonymous", False)
        if tok[0] == "op" and tok[1] == "{":
            return self.table()
        return self.suffixed()

    def table(self):
        line = self.expect("{")[2]
        items = []          # (key expr or None for positional, value expr)
        while not self.is_("}"):
            if self.is_("["):
                self.next(); k = self.expr(); self.expect("]"); self.expect("="); items.append((k, self.expr()))
            elif self.peek()[0] == "name" and self.is_("=", 1):
                k = ("const", line, self.name()); self.next(); items.append((k, self.expr()))
            else:
                items.append((None, self.expr()))
            if self.is_(",") or self.is_(";"):
                self.next()
            elif not self.is_("}"):
                tok = self.peek()
                raise LuaSyntaxError(f"line {tok[2]}: '}}' expected near '{tok[1]}'")
        self.next()
        return ("table", line, items)

    def suffixed(self):
        tok = self.next()
        if tok[0] == "name":
            e = ("name", tok[2], tok[1])
        elif tok[0] == "op" and tok[1] == "(":
            e = ("paren", tok[2], self.expr())
            self.expect(")")
        else:
            raise LuaSyntaxError(f"line {tok[2]}: unexpected symbol near '{tok[1]}'")
        while True:
            t = self.peek()
            if t[0] == "op" and t[1] == ".":
                self.next()
                e = ("index", t[2], e, ("const", t[2], self.name()))
            elif t[0] == "op" and t[1] == "[":
                self.next(); k = self.expr(); self.expect("]")
                e = ("index", t[2], e, k)
            elif t[0] == "op" and t[1] == ":":
                self.next(); n = self.name()
                e = ("method", t[2], e, n, self.args())
            elif (t[0] == "op" and t[1] in ("(", "{")) or t[0] == "string":
                e = ("call", t[2], e, self.args())
            else:
                return e

    def args(self):
        tok = self.peek()
        if tok[0] == "string":
            self.next()
            return [("const", tok[2], _unescape(tok[1]))]
        if tok[0] == "op" and tok[1] == "{":
            return [self.table()]
        self.expect("(")
        out = [] if self.is_(")") else self.explist()
        self.expect(")")
        return out


def parse(src):
    return _P(src).chunk()


# ---------------------------------------------------------------- interpreter

class _Env:
    __slots__ = ("vars", "parent", "varargs")

    def __init__(self, parent=None, varargs=None):
        self.vars, self.parent, self.varargs = {}, parent, varargs

    def find(self, n):
        e = self
        while e is not None:
            if n in e.vars:
                return e
            e = e.parent
        return None


class Interpreter:
    def __init__(self):
        self.globals = {}
        self.stubbed = {}          # name -> times called (globals nothing defines)
        self.chunk_name = "?"
        self.steps = 0
        self.max_steps = 5_000_000
        self._install_base()

    # -- host side
    def run(self, src, name="chunk"):
        old = self.chunk_name
        self.chunk_name = name
        try:
            body = parse(src)
            env = _Env(varargs=[])
            self.steps = 0
            try:
                self.exec_block(body, env)
            except _Return:
                pass
        finally:
            self.chunk_name = old

    def call(self, name, *args):
        """Call a global function, as the game calls a callback: the step
        limit counts from here."""
        f = self.globals.get(name)
        if f is None:
            raise LuaError(f"no global function {name}")
        self.steps = 0
        return self.call_value(f, list(args), 0)

    # -- base library (the parts the game's scripts use)
    def _install_base(self):
        g = self.globals

        def lua_pairs(t, *_):
            return (g["next"], t, None)

        def lua_next(t, k=None, *_):
            if not isinstance(t, LuaTable):
                raise LuaError("bad argument to next (table expected)")
            keys = list(t.d.keys())
            if k is None:
                i = 0
            else:
                kk = int(k) if isinstance(k, float) and k.is_integer() else k
                try:
                    i = keys.index(kk) + 1
                except ValueError:
                    return (None,)
            if i >= len(keys):
                return (None,)
            key = keys[i]
            return (float(key) if isinstance(key, int) else key, t.d[key])

        def lua_ipairs(t, *_):
            def it(tt, i):
                i = int(i) + 1
                v = tt.get(i)
                return (None,) if v is None else (float(i), v)
            return (it, t, 0.0)

        def lua_tostring(v=None, *_):
            return (tostr(v),)

        def lua_tonumber(v=None, *_):
            try:
                return (float(v),)
            except (TypeError, ValueError):
                return (None,)

        def lua_type(v=None, *_):
            return (_type(v),)

        def lua_assert(v=None, msg=None, *rest):
            if not truthy(v):
                raise LuaError(msg or "assertion failed!")
            return (v, msg) + rest

        def lua_unpack(t, *_):
            return tuple(t.get(i) for i in range(1, t.getn() + 1))

        def lua_error(msg=None, *_):
            raise LuaError(tostr(msg))

        def tinsert(t, a, b=None, *_):
            if b is None:
                t.set(t.getn() + 1, a)
            else:
                n, pos = t.getn(), int(a)
                for i in range(n, pos - 1, -1):
                    t.set(i + 1, t.get(i))
                t.set(pos, b)
            return ()

        def tremove(t, pos=None, *_):
            n = t.getn()
            if n == 0:
                return (None,)
            pos = n if pos is None else int(pos)
            v = t.get(pos)
            for i in range(pos, n):
                t.set(i, t.get(i + 1))
            t.set(n, None)
            return (v,)

        def tgetn(t, *_):
            return (float(t.getn()),)

        def tconcat(t, sep="", *_):
            return ((sep or "").join(tostr(t.get(i)) for i in range(1, t.getn() + 1)),)

        g.update({"pairs": lua_pairs, "next": lua_next, "ipairs": lua_ipairs, "tostring": lua_tostring,
                  "tonumber": lua_tonumber, "type": lua_type, "assert": lua_assert, "unpack": lua_unpack,
                  "error": lua_error, "print": lambda *a: (),
                  "table": LuaTable({"insert": tinsert, "remove": tremove, "getn": tgetn, "concat": tconcat})})

    # -- calls
    def call_value(self, f, args, line):
        if isinstance(f, LuaFunction):
            return self.call_function(f, args)
        if callable(f):
            r = f(*args)
            return list(r) if r is not None else []
        raise LuaError(f"{self.chunk_name} line {line}: attempt to call a {_type(f)} value")

    def call_function(self, f, args):
        old = self.chunk_name
        self.chunk_name = f.chunk
        try:
            return self._call_function(f, args)
        except LuaError as e:
            if not getattr(e, "where", None):
                e.where = f"{f.chunk}: {f.name}"
                e.args = (f"{e.where}: {e.args[0]}",)
            raise
        finally:
            self.chunk_name = old

    def _call_function(self, f, args):
        env = _Env(f.env)
        for i, p in enumerate(f.params):
            env.vars[p] = args[i] if i < len(args) else None
        if f.vararg:
            extra = args[len(f.params):]
            env.varargs = extra
            t = LuaTable({i + 1: v for i, v in enumerate(extra) if v is not None})
            t.set("n", float(len(extra)))
            env.vars["arg"] = t
        try:
            self.exec_block(f.body, env)
        except _Return as r:
            return r.values
        return []

    # -- statements
    def exec_block(self, stats, env):
        for s in stats:
            self.exec(s, env)

    def tick(self, line):
        self.steps += 1
        if self.steps > self.max_steps:
            raise LuaError(f"{self.chunk_name} line {line}: too many steps (a loop that never ends?)")

    def exec(self, s, env):
        self.tick(s[1])
        k = s[0]
        if k == "callstat":
            self.eval_multi(s[2], env)
        elif k == "assign":
            vals = self.eval_list(s[3], env)
            for i, t in enumerate(s[2]):
                self.assign(t, vals[i] if i < len(vals) else None, env)
        elif k == "local":
            vals = self.eval_list(s[3], env)
            for i, n in enumerate(s[2]):
                env.vars[n] = vals[i] if i < len(vals) else None
        elif k == "if":
            for cond, body in s[2]:
                if truthy(self.eval(cond, env)):
                    self.exec_block(body, _Env(env, env.varargs))
                    return
            if s[3] is not None:
                self.exec_block(s[3], _Env(env, env.varargs))
        elif k == "fornum":
            a = _num(self.eval(s[3], env), s[1])
            b = _num(self.eval(s[4], env), s[1])
            c = _num(self.eval(s[5], env), s[1]) if s[5] is not None else 1.0
            i = a
            try:
                while (c > 0 and i <= b) or (c <= 0 and i >= b):
                    self.tick(s[1])
                    e = _Env(env, env.varargs)
                    e.vars[s[2]] = i
                    self.exec_block(s[6], e)
                    i += c
            except _Break:
                pass
        elif k == "forin":
            vals = self.eval_list(s[3], env)
            f = vals[0] if vals else None
            if isinstance(f, LuaTable):          # Lua 5.0: for k, v in t do
                state, ctl, f = f, None, self.globals["next"]
            else:
                state = vals[1] if len(vals) > 1 else None
                ctl = vals[2] if len(vals) > 2 else None
            try:
                while True:
                    self.tick(s[1])
                    r = self.call_value(f, [state, ctl], s[1])
                    if not r or r[0] is None:
                        break
                    ctl = r[0]
                    e = _Env(env, env.varargs)
                    for i, n in enumerate(s[2]):
                        e.vars[n] = r[i] if i < len(r) else None
                    self.exec_block(s[4], e)
            except _Break:
                pass
        elif k == "while":
            try:
                while truthy(self.eval(s[2], env)):
                    self.tick(s[1])
                    self.exec_block(s[3], _Env(env, env.varargs))
            except _Break:
                pass
        elif k == "repeat":
            try:
                while True:
                    self.tick(s[1])
                    e = _Env(env, env.varargs)
                    self.exec_block(s[2], e)
                    if truthy(self.eval(s[3], e)):
                        break
            except _Break:
                pass
        elif k == "do":
            self.exec_block(s[2], _Env(env, env.varargs))
        elif k == "funcstat":
            f = self.make_function(s[3], env)
            path = s[2]
            if len(path) == 1:
                self.set_name(path[0], f, env)
            else:
                t = self.get_name(path[0], env, s[1])
                for p in path[1:-1]:
                    t = self.index(t, p, s[1])
                self.setindex(t, path[-1], f, s[1])
        elif k == "localfunc":
            env.vars[s[2]] = None
            env.vars[s[2]] = self.make_function(s[3], env)
        elif k == "return":
            raise _Return(self.eval_list(s[2], env))
        elif k == "break":
            raise _Break()
        else:
            raise LuaError(f"unknown statement {k}")

    def make_function(self, node, env):
        _, line, params, vararg, body, name = node
        return LuaFunction(params, vararg, body, env, name, self, self.chunk_name)

    def assign(self, target, v, env):
        if target[0] == "name":
            self.set_name(target[2], v, env)
        else:
            t = self.eval(target[2], env)
            self.setindex(t, self.eval(target[3], env), v, target[1])

    def set_name(self, n, v, env):
        e = env.find(n)
        if e is not None:
            e.vars[n] = v
        elif v is None:
            self.globals.pop(n, None)
        else:
            self.globals[n] = v

    def get_name(self, n, env, line):
        e = env.find(n)
        if e is not None:
            return e.vars[n]
        if n in self.globals:
            return self.globals[n]
        return None

    def index(self, t, k, line):
        if isinstance(t, LuaTable):
            return t.get(k)
        raise LuaError(f"{self.chunk_name} line {line}: attempt to index a {_type(t)} value (field {tostr(k)})")

    def setindex(self, t, k, v, line):
        if not isinstance(t, LuaTable):
            raise LuaError(f"{self.chunk_name} line {line}: attempt to index a {_type(t)} value")
        t.set(k, v)

    # -- expressions
    def eval_list(self, exprs, env):
        out = []
        for i, e in enumerate(exprs):
            if i == len(exprs) - 1:
                out.extend(self.eval_multi(e, env))
            else:
                out.append(self.eval(e, env))
        return out

    def eval_multi(self, e, env):
        k = e[0]
        if k == "call":
            f = self.eval(e[2], env)
            if f is None and e[2][0] == "name":
                return self.stub(e[2][2], self.eval_list(e[3], env))
            return self.call_value(f, self.eval_list(e[3], env), e[1])
        if k == "method":
            obj = self.eval(e[2], env)
            f = self.index(obj, e[3], e[1])
            return self.call_value(f, [obj] + self.eval_list(e[4], env), e[1])
        if k == "vararg":
            return list(env_varargs(env))
        return [self.eval(e, env)]

    def stub(self, name, args):
        self.stubbed[name] = self.stubbed.get(name, 0) + 1
        return []

    def eval(self, e, env):
        k = e[0]
        if k == "const":
            return e[2]
        if k == "name":
            return self.get_name(e[2], env, e[1])
        if k == "index":
            return self.index(self.eval(e[2], env), self.eval(e[3], env), e[1])
        if k in ("call", "method", "vararg"):
            r = self.eval_multi(e, env)
            return r[0] if r else None
        if k == "paren":
            return self.eval(e[2], env)
        if k == "function":
            return self.make_function(e, env)
        if k == "table":
            t = LuaTable()
            n = 0
            items = e[2]
            for i, (ke, ve) in enumerate(items):
                if ke is None:
                    if i == len(items) - 1 and ve[0] in ("call", "method", "vararg"):
                        for v in self.eval_multi(ve, env):
                            n += 1
                            t.set(n, v)
                    else:
                        n += 1
                        t.set(n, self.eval(ve, env))
                else:
                    t.set(self.eval(ke, env), self.eval(ve, env))
            return t
        if k == "not":
            return not truthy(self.eval(e[2], env))
        if k == "neg":
            return -_num(self.eval(e[2], env), e[1])
        if k == "binop":
            op = e[2]
            if op == "and":
                a = self.eval(e[3], env)
                return self.eval(e[4], env) if truthy(a) else a
            if op == "or":
                a = self.eval(e[3], env)
                return a if truthy(a) else self.eval(e[4], env)
            a, b = self.eval(e[3], env), self.eval(e[4], env)
            return self.binop(op, a, b, e[1])
        raise LuaError(f"unknown expression {k}")

    def binop(self, op, a, b, line):
        if op == "..":
            for v in (a, b):
                if not isinstance(v, (str, int, float)) or isinstance(v, bool):
                    raise LuaError(f"{self.chunk_name} line {line}: attempt to concatenate a {_type(v)} value")
            return tostr(a) + tostr(b)
        if op == "==":
            return _eq(a, b)
        if op == "~=":
            return not _eq(a, b)
        if op in ("<", "<=", ">", ">="):
            if isinstance(a, bool) or isinstance(b, bool) or not (
                    (isinstance(a, (int, float)) and isinstance(b, (int, float))) or
                    (isinstance(a, str) and isinstance(b, str))):
                raise LuaError(f"{self.chunk_name} line {line}: attempt to compare {_type(a)} with {_type(b)}")
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        x, y = _num(a, line), _num(b, line)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                return float("inf") if x > 0 else float("-inf") if x < 0 else float("nan")
            return x / y
        if op == "^":
            return x ** y
        raise LuaError(f"unknown operator {op}")


def env_varargs(env):
    e = env
    while e is not None:
        if e.varargs is not None:
            return e.varargs
        e = e.parent
    return []


def _eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, (LuaTable, LuaFunction)) or isinstance(b, (LuaTable, LuaFunction)):
        return a is b
    return a == b
