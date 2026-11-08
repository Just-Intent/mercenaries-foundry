"""Recover names for hashed keys and types.

Names are hashed, so we can't read them back, but we can test guesses. Any
printable string in the game executable or data is a guess; a 32-bit hash
match against a real key is strong evidence the guess is right.
"""
import re
from pathlib import Path

from .hashing import NameBook, name_hash

# Plausible type names. A hash match against a real type ID marks it as a
# likely name; confirm by looking at the records before adding to KNOWN_TYPES.
TYPE_GUESSES = """
texture tex image bitmap model msh mesh geometry geom skeleton skel bone
animation anim animbank sound sfx audio music wave bank soundbank stream
font shader effect fx particle decal light lighting material collision
havok physics ragdoll terrain ter heightmap sky water foliage path navmesh
zone movie video config cfg table string locale localization ai behavior
odf class template blueprint entity prop object instance layer level mission
ui menu hud sprite icon cursor map minimap grid index lua scr wrld reg
""".split()

_RUN = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_./\\:\- ]{2,95}")


def strings_in(data: bytes):
    """Printable ASCII runs, plus their words, path pieces and extension-less forms."""
    for m in _RUN.finditer(data):
        text = m.group().decode("ascii").strip()
        if len(text) < 3:
            continue
        pieces = {text}
        pieces.update(text.split())
        for p in list(pieces):
            pieces.update(re.split(r"[\\/]", p))
        for p in list(pieces):
            stem, dot, ext = p.rpartition(".")
            if dot and stem and ext.isalnum() and len(ext) <= 4:
                pieces.add(stem)
        for p in pieces:
            if len(p) >= 3:
                yield p


def _blobs(path):
    """File contents to scan. For archives: each record, with scripts decompressed."""
    path = Path(path)
    if path.suffix.lower() != ".dsk":
        yield path.read_bytes()
        return
    from . import dsk, script
    arc = dsk.Archive(path)
    kind = name_hash("script")
    with arc.path.open("rb") as f:
        for r in arc.records:
            f.seek(r.offset)
            payload = f.read(r.size)
            yield payload
            if r.kind == kind:
                try:
                    yield script.decode(payload).text
                except (ValueError, KeyError):
                    pass


def harvest(paths, wanted=None) -> NameBook:
    """Collect candidate names from files (executables, data, archives).
    With `wanted`, keep only names whose hash is in that set."""
    book = NameBook()
    for path in paths:
        for data in _blobs(path):
            _scan(data, book, wanted)
    return book


def _scan(data, book, wanted):
    for text in strings_in(data):
        try:
            key = name_hash(text)
        except (ValueError, UnicodeEncodeError):
            continue
        if wanted is None or key in wanted:
            book.add(text)


def guess_types(kinds) -> dict:
    """Map type IDs to guessed names from TYPE_GUESSES."""
    table = {name_hash(n): n for n in TYPE_GUESSES}
    return {k: table[k] for k in kinds if k in table}


def write(book: NameBook, path) -> int:
    lines = sorted({n for _, n in book.items()}, key=str.lower)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)
