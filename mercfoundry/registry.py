"""Localized text records (type "registry"): ucfb -> reg_.

Retail layout (english.dsk, all 91 records):

    reg_
      INFO   "name\\0english\\0num_components\\0<n>\\0"
      SCPE / BIN_ ...
    SCPE     INFO "name\\0<name>\\0", DATA = more SCPE / BIN_
    BIN_     INFO "name\\0<name>\\0", DATA = UTF-16LE text, no terminator

num_components is close to the number of entries but not equal to it (it
runs 1-7 higher); we raise it by the number of entries we add.

Strings are exposed as dotted keys ("vo.ap1.49977AP1"). Editing changes only
the touched values; everything else is kept byte-for-byte.
"""
import re

from . import ucfb

FORMAT = "mercfoundry-text-1"


class Node:
    def __init__(self, tag, fields, pad_last):
        self.tag = tag            # b"SCPE" or b"BIN_"
        self.fields = fields      # [(tag, body)] in original order
        self.pad_last = pad_last
        self.children = None      # for scopes: [list of Node | (tag, body), pad_last]

    @classmethod
    def new(cls, tag, name, pad_last=True):
        node = cls(tag, [(b"INFO", b"name\0" + name.encode("ascii") + b"\0"), (b"DATA", b"")], pad_last)
        if tag == b"SCPE":
            node.children = [[], True]
        return node

    def field(self, tag):
        for t, b in self.fields:
            if t == tag:
                return b
        return None

    def set_field(self, tag, body):
        self.fields = [(t, body if t == tag else b) for t, b in self.fields]

    @property
    def name(self):
        parts = (self.field(b"INFO") or b"").split(b"\0")
        if len(parts) < 3 or parts[0] != b"name":
            raise ValueError("registry entry has no name in INFO")
        return parts[1].decode("ascii")

    def text(self):
        data = self.field(b"DATA") or b""
        if len(data) % 2:
            return None
        try:
            text = data.decode("utf-16-le")
        except UnicodeDecodeError:
            return None
        return text[:-1] if text.endswith("\0") else text

    def set_text(self, text):
        old = self.field(b"DATA") or b""
        nul = "\0" if old.endswith(b"\0\0") and len(old) % 2 == 0 else ""
        self.set_field(b"DATA", (text + nul).encode("utf-16-le"))


def _parse_list(data):
    items, pad_last = ucfb.split(data)
    out = []
    for tag, body in items:
        if tag in (b"SCPE", b"BIN_"):
            fields, pad = ucfb.split(body)
            node = Node(tag, fields, pad)
            if tag == b"SCPE":
                node.children = list(_parse_list(node.field(b"DATA") or b""))
            out.append(node)
        else:
            out.append((tag, body))
    return [out, pad_last]


def _build_list(parsed):
    items, pad_last = parsed
    out = []
    for item in items:
        if isinstance(item, Node):
            if item.tag == b"SCPE":
                item.set_field(b"DATA", _build_list(item.children))
            out.append((item.tag, ucfb.pack_children(item.fields, item.pad_last)))
        else:
            out.append(item)
    return ucfb.pack_children(out, pad_last)


def _numbered(walk):
    """Dotted keys with "#2", "#3"... for repeats, in file order."""
    seen = {}
    for key, node in walk:
        n = seen[key] = seen.get(key, 0) + 1
        yield (key if n == 1 else f"{key}#{n}"), node


class Registry:
    def __init__(self, record: bytes):
        body, self.pad = ucfb.unwrap(record, b"reg_", with_pad=True)
        self.root = _parse_list(body)

    def to_bytes(self) -> bytes:
        return ucfb.wrap(b"reg_", _build_list(self.root), self.pad)

    def walk(self):
        """Yield (dotted key, node) for every value, in file order."""
        def go(parsed, prefix):
            for item in parsed[0]:
                if not isinstance(item, Node):
                    continue
                key = prefix + item.name
                if item.tag == b"SCPE":
                    yield from go(item.children, key + ".")
                else:
                    yield key, item
        yield from go(self.root, "")

    def strings(self) -> dict:
        return {k: n.text() for k, n in _numbered(self.walk()) if n.text() is not None}

    def _value_pad(self):
        for _, node in self.walk():
            return node.pad_last
        return True

    def _scope(self, path):
        """Find or create the scope for a list of names. Returns (children, created)."""
        parsed, created = self.root, 0
        for name in path:
            for item in parsed[0]:
                if isinstance(item, Node) and item.tag == b"SCPE" and item.name.lower() == name.lower():
                    parsed = item.children
                    break
            else:
                node = Node.new(b"SCPE", name, self._value_pad())
                parsed[0].append(node)
                parsed, created = node.children, created + 1
        return parsed, created

    def _bump_components(self, added):
        items = self.root[0]
        for i, item in enumerate(items):
            if not isinstance(item, Node) and item[0] == b"INFO":
                text = item[1].decode("ascii")
                m = re.search(r"num_components\0(\d+)\0", text)
                if m:
                    text = text[:m.start(1)] + str(int(m.group(1)) + added) + text[m.end(1):]
                    items[i] = (b"INFO", text.encode("ascii"))
                return

    def apply(self, edits: dict) -> tuple:
        """Set text by key; keys not in the record are added. Keys left out of
        `edits` are not touched. Returns (changed, added)."""
        by_key = dict(_numbered(self.walk()))
        changed = added = 0
        for key, text in edits.items():
            node = by_key.get(key)
            if node is not None:
                if node.text() != text:
                    node.set_text(text)
                    changed += 1
                continue
            if "#" in key:
                raise KeyError(f"can't add a numbered repeat: {key}")
            *path, name = key.split(".")
            if not name:
                raise KeyError(f"bad text key: {key!r}")
            parsed, scopes = self._scope(path)
            node = Node.new(b"BIN_", name, self._value_pad())
            node.set_text(text)
            parsed[0].append(node)
            by_key[key] = node
            added += 1 + scopes
        if added:
            self._bump_components(added)
        return changed, added


def to_json(record: bytes) -> dict:
    return {"format": FORMAT, "strings": Registry(record).strings()}


def from_json(doc: dict, base: bytes) -> bytes:
    if doc.get("format") != FORMAT:
        raise ValueError("not a mercfoundry text file")
    reg = Registry(base)
    reg.apply(doc["strings"])
    return reg.to_bytes()
