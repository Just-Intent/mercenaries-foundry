"""Read Xbox disc images (XDVDFS), as .iso or trimmed .xiso files.

The volume descriptor sits 0x10000 bytes into the game partition and starts
with "MICROSOFT*XBOX*MEDIA", followed by the root directory's sector and size.
Full Redump images put the game partition at a fixed offset; trimmed xiso
files start with it.

A directory is a binary tree of entries, each 4-byte aligned:
    u16 left, u16 right    child offsets in 4-byte units (0 = none)
    u32 sector, u32 size
    u8 attributes (0x10 = directory), u8 name length, name
"""
import struct
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
PARTITION_OFFSETS = (0, 0x18300000, 0x0FD90000, 0x089D80000, 0x2080000)
DIRECTORY = 0x10


@dataclass
class Entry:
    path: str
    sector: int
    size: int
    is_dir: bool


class Image:
    def __init__(self, path):
        self.path = Path(path)
        self.f = self.path.open("rb")
        for base in PARTITION_OFFSETS:
            self.f.seek(base + 32 * SECTOR)
            head = self.f.read(28)
            if head[:20] == MAGIC:
                self.base = base
                self.root_sector, self.root_size = struct.unpack_from("<II", head, 20)
                break
        else:
            self.f.close()
            raise ValueError(f"{self.path}: no Xbox volume descriptor found")

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, sector, size, offset=0):
        self.f.seek(self.base + sector * SECTOR + offset)
        return self.f.read(size)

    def _directory(self, sector, size, prefix):
        if size == 0:
            return
        table = self._read(sector, size)
        seen = set()
        stack = [0]
        while stack:
            off = stack.pop()
            if off in seen or off + 14 > len(table):
                continue
            seen.add(off)
            left, right, start, length, attr, nlen = struct.unpack_from("<HHIIBB", table, off)
            if left == 0xFFFF and right == 0xFFFF:
                continue  # padding
            name = table[off + 14:off + 14 + nlen].decode("ascii")
            path = f"{prefix}{name}"
            is_dir = bool(attr & DIRECTORY)
            yield Entry(path, start, length, is_dir)
            if is_dir:
                yield from self._directory(start, length, path + "/")
            if right:
                stack.append(right * 4)
            if left:
                stack.append(left * 4)

    def entries(self):
        return list(self._directory(self.root_sector, self.root_size, ""))

    def read_file(self, entry: Entry, dst):
        self.f.seek(self.base + entry.sector * SECTOR)
        left = entry.size
        while left:
            block = self.f.read(min(left, 1 << 20))
            if not block:
                raise ValueError(f"{entry.path}: image is truncated")
            dst.write(block)
            left -= len(block)


def extract(image_path, out_dir, progress=None) -> int:
    """Extract every file. Returns the number of files written."""
    out = Path(out_dir)
    count = 0
    with Image(image_path) as img:
        for e in img.entries():
            target = out / e.path
            if ".." in Path(e.path).parts:
                raise ValueError(f"unsafe path in image: {e.path}")
            if e.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as dst:
                img.read_file(e, dst)
            count += 1
            if progress:
                progress(e)
    return count
