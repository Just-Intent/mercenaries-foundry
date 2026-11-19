"""The editor's Changes panel: the mod's own git repository (status, commit,
push) and building it as the launcher does (and a playtest zip, from a
toolkit source tree).

The mod is under git when git, run in the mod's folder, finds a repository
whose work tree is that folder (a `.git` folder, or a `.git` file naming a
git dir elsewhere: "gitdir: C:/Dev/mymod.git"). A repository higher up (the
mods folder inside some other checkout) doesn't count: its commits wouldn't
be the mod's.

mod.json "generated" lists files the mod's own tools write, so edits made
by hand (or in the editor) get overwritten when the tools run again:
    "generated": ["encounters/gaza_*.json",
                  {"file": "maps/gaza.json", "keys": ["hqs", "interiors"]}]
A string is a glob (relative to the mod folder, / between folders); an
object marks only some keys of a JSON file as generated.

Commits use the repository's own git identity (user.name and user.email in
its config, or the global one) and the message as given.
"""
import fnmatch
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

GIT_TIMEOUT = 60
PUSH_TIMEOUT = 180


def _git(mod, *args, timeout=GIT_TIMEOUT):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        p = subprocess.run(["git", "-C", str(mod), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise ValueError("git isn't installed (or not on PATH)") from None
    except subprocess.TimeoutExpired:
        raise ValueError(f"git {args[0]} took longer than {timeout} s and was stopped") from None
    return p.returncode, p.stdout, p.stderr


def repo_root(mod):
    """The mod's own work tree (its folder), or None when it isn't under git
    of its own."""
    code, out, _ = _git(mod, "rev-parse", "--show-toplevel")
    if code != 0 or not out.strip():
        return None
    try:
        same = Path(out.strip()).resolve() == Path(mod).resolve()
    except OSError:
        same = False
    return Path(mod).resolve() if same else None


def generated_rules(mod):
    meta = Path(mod) / "mod.json"
    try:
        doc = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    except ValueError:
        return []
    out = []
    for g in doc.get("generated") or []:
        if isinstance(g, str):
            out.append((g.replace("\\", "/"), None))
        elif isinstance(g, dict) and g.get("file"):
            out.append((str(g["file"]).replace("\\", "/"), [str(k) for k in g.get("keys") or []] or None))
    return out


def generated(path, rules):
    """None, True (the whole file is the tools'), or the keys that are."""
    path = path.replace("\\", "/")
    keys = []
    for pattern, ks in rules:
        if fnmatch.fnmatchcase(path.lower(), pattern.lower()):
            if ks is None:
                return True
            keys += [k for k in ks if k not in keys]
    return keys or None


_KIND = {"M": "changed", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "T": "changed", "U": "conflict",
         "?": "new"}


def status(mod):
    """{"git": bool, "files": [{path, kind, generated}], "branch", "upstream",
    "ahead", "behind", "remotes", "identity", "last"}."""
    root = repo_root(mod)
    if root is None:
        return {"git": False, "folder": Path(mod).name}
    code, out, err = _git(root, "status", "--porcelain=v1", "-z", "--branch", "--untracked-files=all")
    if code != 0:
        raise ValueError(f"git status failed: {err.strip()}")
    rules = generated_rules(root)
    files, branch, upstream, ahead, behind = [], None, None, 0, 0
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            info = ""
            if " [" in head:
                head, info = head.split(" [", 1)
            branch, _, upstream = head.partition("...")
            branch = branch.replace("No commits yet on ", "")
            for bit in info.rstrip("]").split(", "):
                if bit.startswith("ahead "):
                    ahead = int(bit[6:])
                elif bit.startswith("behind "):
                    behind = int(bit[7:])
            continue
        xy, path = e[:2], e[3:]
        if "R" in xy or "C" in xy:
            i += 1                        # the old name follows
        code_ = (xy[1] if xy[1] != " " else xy[0])
        files.append({"path": path, "kind": _KIND.get(code_, "changed"), "generated": generated(path, rules)})
    _, remotes, _ = _git(root, "remote")
    _, name, _ = _git(root, "config", "user.name")
    _, email, _ = _git(root, "config", "user.email")
    _, last, _ = _git(root, "log", "-1", "--format=%h %s")
    return {"git": True, "folder": root.name, "files": sorted(files, key=lambda f: f["path"].lower()),
            "branch": branch, "upstream": upstream or None, "ahead": ahead, "behind": behind,
            "remotes": remotes.split(), "identity": f"{name.strip()} <{email.strip()}>" if name.strip() else None,
            "last": last.strip() or None}


def commit(mod, message, paths):
    root = repo_root(mod)
    if root is None:
        raise ValueError(f"mods/{Path(mod).name} isn't under git of its own")
    message = (message or "").strip()
    if not message:
        raise ValueError("write a message for the commit")
    paths = [str(p) for p in paths or []]
    if not paths:
        raise ValueError("pick at least one file to commit")
    for p in paths:
        if Path(p).is_absolute() or ".." in Path(p).parts:
            raise ValueError(f"{p!r} isn't a path in the mod")
    code, _, err = _git(root, "config", "user.name")
    if code != 0:
        raise ValueError("git has no identity for this repository: set user.name and user.email "
                         "(git config, in the mod's folder)")
    code, _, err = _git(root, "add", "-A", "--", *paths)
    if code != 0:
        raise ValueError(f"git add failed: {err.strip()}")
    code, out, err = _git(root, "commit", "-m", message, "--", *paths)
    if code != 0:
        raise ValueError(f"git commit failed: {(err or out).strip()}")
    _, last, _ = _git(root, "log", "-1", "--format=%h %s")
    return {"committed": last.strip(), **status(root)}


def push(mod):
    root = repo_root(mod)
    if root is None:
        raise ValueError(f"mods/{Path(mod).name} isn't under git of its own")
    st = status(root)
    if st["upstream"]:
        args = ["push"]
    elif st["remotes"]:
        remote = "origin" if "origin" in st["remotes"] else st["remotes"][0]
        args = ["push", "-u", remote, "HEAD"]
    else:
        raise ValueError("the mod's repository has no remote to push to (git remote add origin <url>)")
    code, out, err = _git(root, *args, timeout=PUSH_TIMEOUT)
    if code != 0:
        raise ValueError(f"git push failed: {(err or out).strip()}")
    return {"pushed": (err or out).strip().splitlines()[-1:] or [""], **status(root)}


# ------------------------------------------------------------ building
def _toolkit_path():
    """What to put on PYTHONPATH so a child Python imports this toolkit: the
    .pyz it runs from, or the source tree's root."""
    return str(Path(__file__).resolve().parents[2])


def playtest_script():
    """packaging/playtest.py of a toolkit source tree (not in the .pyz)."""
    p = Path(__file__).resolve().parents[2] / "packaging" / "playtest.py"
    return p if p.exists() else None


class Builds:
    """One build at a time, in the background: the launcher's mod sync for the
    game folder, then (if asked) a playtest zip. Its lines are kept for the
    panel to show."""

    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    def state(self):
        with self.lock:
            return dict(self.job) if self.job else {"running": False}

    def start(self, game, playtest=False):
        if game is None:
            raise ValueError("the editor isn't in a game's mods folder, so there's no launcher build to run")
        with self.lock:
            if self.job and self.job["running"]:
                raise ValueError("a build is already running")
            self.job = {"running": True, "step": "build", "lines": [], "ok": None, "started": time.time(),
                        "zip": None}
        steps = [("build", [sys.executable, "-m", "mercfoundry", "mod", "sync", str(game), "--enable"], None)]
        if playtest:
            script = playtest_script()
            if script is None:
                raise ValueError("a playtest zip needs the toolkit's source tree (packaging/playtest.py)")
            steps.append(("playtest", [sys.executable, str(script)], script.parent.parent))
        threading.Thread(target=self._run, args=(steps,), daemon=True).start()
        return self.state()

    def _say(self, line):
        with self.lock:
            self.job["lines"].append(line)
            del self.job["lines"][:-400]

    def _run(self, steps):
        env = dict(os.environ, PYTHONPATH=_toolkit_path(), PYTHONIOENCODING="utf-8")
        ok = True
        for step, cmd, cwd in steps:
            with self.lock:
                self.job["step"] = step
            self._say(f"-- {step}: {' '.join(Path(c).name if i < 2 else c for i, c in enumerate(cmd))}")
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", env=env, cwd=cwd,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in p.stdout:
                    line = line.rstrip()
                    if line:
                        self._say(line)
                        if step == "playtest" and line.startswith("wrote ") and ".zip" in line:
                            with self.lock:       # "wrote <path>.zip (136 MB)"
                                self.job["zip"] = line[len("wrote "):line.rindex(".zip") + 4]
                ok = p.wait() == 0
            except OSError as e:
                self._say(f"couldn't run it: {e}")
                ok = False
            if not ok:
                self._say(f"-- {step} failed")
                break
        with self.lock:
            self.job["running"] = False
            self.job["ok"] = ok
            self.job["step"] = "done" if ok else self.job["step"]
