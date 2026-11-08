"""Localized text records (type "registry"): ucfb -> reg_.

reg_ holds nested SCPE (scope) and BIN_ (value) chunks. Each has INFO, which
starts "name\\0<name>\\0", and DATA: child chunks for a scope, or the value for
a BIN_. Text values are UTF-16LE, NUL-terminated. Observed in retail
english.dsk by Mercenaries Recompiled (inspect_retail_templates.py).

Strings are exposed as dotted keys ("scope.sub.name"). Editing changes only
the DATA of the touched values; everything else is kept byte-for-byte.
"""
from . import ucfb

FORMAT = "mercfoundry-text-1"


class Node:
    def __init__(self, tag, fields, pad_last):
        self.tag = tag            # b"SCPE" or b"BIN_"
        self.fields = fields      # [(tag, body)] in original order
        self.pad_last = pad_last
        self.children = None      # for scopes: (list[Node | (tag, body)], pad_last)

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
        if len(data) % 2 or not data.endswith(b"\0\0"):
            return None
        try:
            return data.decode("utf-16-le")[:-1]
        except UnicodeDecodeError:
            return None


def _parse_list(data):
    items, pad_last = ucfb.split(data)
    out = []
    for tag, body in items:
        if tag in (b"SCPE", b"BIN_"):
            fields, pad = ucfb.split(body)
            node = Node(tag, fields, pad)
            if tag == b"SCPE":
                node.children = _parse_list(node.field(b"DATA") or b"")
            out.append(node)
        else:
            out.append((tag, body))
    return out, pad_last


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
        """Text values by key. Repeated keys get "#2", "#3"... suffixes."""
        out, seen = {}, {}
        for key, node in self.walk():
            text = node.text()
            if text is None:
                continue
            n = seen[key] = seen.get(key, 0) + 1
            out[key if n == 1 else f"{key}#{n}"] = text
        return out

    def apply(self, edits: dict) -> int:
        """Set text values by key. Returns how many changed.

        Keys must already exist; adding new strings isn't supported yet
        because the rest of INFO isn't understood.
        """
        by_key, seen = {}, {}
        for key, node in self.walk():
            if node.text() is None:
                continue
            n = seen[key] = seen.get(key, 0) + 1
            by_key[key if n == 1 else f"{key}#{n}"] = node
        missing = [k for k in edits if k not in by_key]
        if missing:
            raise KeyError(f"unknown text keys: {', '.join(missing[:5])}")
        changed = 0
        for key, text in edits.items():
            node = by_key[key]
            if node.text() != text:
                node.set_field(b"DATA", (text + "\0").encode("utf-16-le"))
                changed += 1
        return changed


def to_json(record: bytes) -> dict:
    return {"format": FORMAT, "strings": Registry(record).strings()}


def from_json(doc: dict, base: bytes) -> bytes:
    if doc.get("format") != FORMAT:
        raise ValueError("not a mercfoundry text file")
    reg = Registry(base)
    reg.apply(doc["strings"])
    return reg.to_bytes()
