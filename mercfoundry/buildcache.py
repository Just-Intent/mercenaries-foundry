"""A cache of the build's slow, pure steps, keyed by their input's content.

Texture compression (DXT, in pure Python) and the game's script and model
compression take most of a big mod's build. Their results depend only on
their input bytes and on the code that makes them, so they're kept:

- in memory for the build (a model decoded ten times is decompressed once),
- on disk between builds (configure(folder)): a mod built again after an
  edit or a toolkit update re-encodes only what changed. `mod sync` keeps it
  in the game's foundry/cache, so the launcher's next preparation, and a
  playtest package that carries the folder, start warm.

A key is a hash of the step's name, the source of the module that does it
(any change to the encoder makes new keys) and the input. A file that can't
be read or written is skipped: the cache never changes what a build makes.
"""
import hashlib
import os
import time
from pathlib import Path

_memory = {}
_memory_bytes = 0
_MEMORY_LIMIT = 768 * 1024 * 1024       # past this, the oldest results are dropped
_dir = None
_code = {}
stats = {"hits": 0, "disk_hits": 0, "misses": 0}
PRUNE_DAYS = 60                          # disk entries not used for this long go


def configure(folder):
    """Keep results on disk in folder (None: memory only)."""
    global _dir
    _dir = Path(folder) if folder else None
    if _dir:
        try:
            _dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            _dir = None


def default_folder():
    """Where `mod build` keeps its cache unless MERCFOUNDRY_CACHE says (an
    empty value: none on disk)."""
    env = os.environ.get("MERCFOUNDRY_CACHE")
    if env is not None:
        return env or None
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mercfoundry", "cache")


def _code_hash(module):
    """The source of the module doing the step (from the .pyz too)."""
    h = _code.get(module.__name__)
    if h is None:
        try:
            loader = module.__spec__.loader
            src = loader.get_data(module.__file__) if hasattr(loader, "get_data") else open(module.__file__, "rb").read()
        except (OSError, AttributeError, TypeError):
            src = module.__name__.encode()
        h = _code[module.__name__] = hashlib.sha1(src).digest()
    return h


def _remember(key, value):
    global _memory_bytes
    _memory[key] = value
    _memory_bytes += len(value)
    while _memory_bytes > _MEMORY_LIMIT and _memory:
        old = next(iter(_memory))
        _memory_bytes -= len(_memory.pop(old))


def cached(step, module, parts, make, disk=True):
    """make() (bytes) for these input parts (bytes or str or ints), cached."""
    h = hashlib.sha1(step.encode() + b"\0" + _code_hash(module))
    for p in parts:
        if isinstance(p, str):
            p = p.encode("utf-8")
        elif isinstance(p, int):
            p = str(p).encode()
        h.update(len(p).to_bytes(8, "little"))
        h.update(p)
    key = h.hexdigest()
    hit = _memory.get(key)
    if hit is not None:
        stats["hits"] += 1
        return hit
    path = _dir / key[:2] / key if (disk and _dir) else None
    if path is not None:
        try:
            value = path.read_bytes()
            stats["disk_hits"] += 1
            try:
                os.utime(path)               # in use: kept by prune()
            except OSError:
                pass
            _remember(key, value)
            return value
        except OSError:
            pass
    stats["misses"] += 1
    value = make()
    _remember(key, value)
    if path is not None:
        try:
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix(".tmp%d" % os.getpid())
            tmp.write_bytes(value)
            os.replace(tmp, path)
        except OSError:
            pass
    return value


def prune(days=PRUNE_DAYS):
    """Disk entries no build has used for days go. Returns how many."""
    if not _dir or not _dir.is_dir():
        return 0
    cut = time.time() - days * 86400
    n = 0
    for sub in _dir.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            try:
                if f.stat().st_mtime < cut:
                    f.unlink()
                    n += 1
            except OSError:
                pass
    return n


def summary():
    return (f"build cache: {stats['hits'] + stats['disk_hits']} results reused "
            f"({stats['disk_hits']} from earlier builds), {stats['misses']} made")
