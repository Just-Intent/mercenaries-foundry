"""Asset-name hashing used by Mercenaries archives.

Keys and type IDs in .dsk archives are 32-bit FNV-1a hashes of the name,
folded to lowercase by OR-ing each byte with 0x20. Verified against the
known type names "script" (0x203E6FAA), "world" (0x37A3E893) and
"registry" (0x3884598E).
"""

FNV_OFFSET = 2166136261
FNV_PRIME = 16777619


def name_hash(name: str) -> int:
    """Hash an asset or type name the way the game does. Empty name -> 0."""
    if not name:
        return 0
    if "\0" in name:
        raise ValueError("asset names cannot contain NUL")
    value = FNV_OFFSET
    for byte in name.encode("ascii"):
        value = ((value ^ (byte | 0x20)) * FNV_PRIME) & 0xFFFFFFFF
    return value


class NameBook:
    """Maps hashes back to names we have seen or been told about."""

    def __init__(self):
        self._names: dict[int, str] = {}

    def add(self, name: str) -> int:
        key = name_hash(name)
        if key:
            self._names.setdefault(key, name)
        return key

    def add_many(self, names) -> None:
        for name in names:
            name = name.strip()
            if name and not name.startswith("#"):
                self.add(name)

    def get(self, key: int):
        return self._names.get(key)

    def __len__(self):
        return len(self._names)

    def items(self):
        return self._names.items()


# Type names confirmed by hash. All but the first three were found by
# harvesting strings from the retail executable and data whose hash matches
# a real type ID; "segments" by a guess whose hash matched, with contents
# (CSEG -> segm chunks) to back it. Spelling follows the game where it has one.
KNOWN_TYPES = [
    "script", "world", "registry",
    "Model", "Texture", "animation", "segments", "Terrain", "path", "list",
    "encounter", "region", "lineregion", "animtable", "soundEffect", "font",
    "icon", "sprite", "effects", "effectstable", "TGA", "TXT",
]


def type_book() -> NameBook:
    book = NameBook()
    book.add_many(KNOWN_TYPES)
    return book
