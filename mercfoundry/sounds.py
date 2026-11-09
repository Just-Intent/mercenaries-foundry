"""Game sounds for mods: export cues as .wav, replace them from a mod.

A mod replaces sounds with .wav files named after the cue the game plays:

    sounds/<sound bank>/<cue>.wav       every wave the cue can play
    sounds/<sound bank>/<cue>.<n>.wav   only its n-th wave (cues that pick
                                        one of several at random, or play
                                        several in turn)

    sounds/<sound bank>/new/<cue>.wav   a new cue in that bank

e.g. sounds/fio/SWNfio01.wav for Audio_PlayVoiceover('fio.SWNfio01'). The
build converts each .wav to the replaced wave's own format (Xbox ADPCM,
its channel count and sample rate), rebuilds that wave bank from the
player's copy and writes it to the mod as a loose DATAxbox/SOUND file.
`sound export` writes cues under the same names, so an exported file put
back in place round-trips.

A new cue is added in three places, all built from the player's files: its
wave at the end of the wave bank the sound bank plays from, the cue and a
one-wave sound at the end of the sound bank, and the game's own entries for
it (an xcl_ cue and xsl_ sound chunk, copied from one of the bank's simple
cues) in the assets.dsk record that lists the bank's cues. Scripts then play
it like any other: Audio_PlayVoiceover('fio.MyLine01').
"""
from pathlib import Path

from . import ucfb, xsb, xwb

REGISTRY = 0xFA83847D       # assets.dsk records listing every cue (xcl_/xch_) and sound (xsl_/xsh_)


def targets(lib: xsb.Library, bank: str, stem: str):
    """(cue, [(wave bank, wave index)]) a mod file name points at."""
    sb = lib.sound_bank(bank)
    cue = sb.cue(stem)
    if cue:
        return cue, sb.cue_waves(cue)
    base, _, n = stem.rpartition(".")
    cue = sb.cue(base) if base and n.isdigit() else None
    if cue is None:
        raise ValueError(f"sound bank {bank!r} has no cue {stem!r}")
    waves = sb.cue_waves(cue)
    if not 1 <= int(n) <= len(waves):
        raise ValueError(f"cue {cue.name} plays {len(waves)} wave{'s' if len(waves) != 1 else ''}, not {n}")
    return cue, [waves[int(n) - 1]]


def file_names(sb: xsb.SoundBank, cue):
    """[(file stem, (wave bank, index))] a cue exports as."""
    waves = sb.cue_waves(cue)
    if len(waves) == 1:
        return [(cue.name, waves[0])]
    return [(f"{cue.name}.{i + 1}", w) for i, w in enumerate(waves)]


def decoded(lib: xsb.Library, wave_bank: str, index: int) -> bytes:
    """A wave as a .wav file."""
    wb = lib.wave_bank(wave_bank)
    e = wb.entries[index]
    return xwb.wav(xwb.to_pcm16(wb, e), e.channels, e.rate)


def same_as_game(lib: xsb.Library, wave_bank: str, index: int, wav_data: bytes) -> bool:
    """Whether a .wav is exactly the game's wave (an unchanged export)."""
    wb = lib.wave_bank(wave_bank)
    e = wb.entries[index]
    channels, rate, pcm = xwb.read_wav(wav_data)
    return (channels, rate) == (e.channels, e.rate) and pcm == xwb.to_pcm16(wb, e)


def unchanged(lib: xsb.Library, path) -> bool:
    """Whether a mod's sounds/<bank>/<cue>.wav is the game's own sound."""
    path = Path(path)
    _, waves = targets(lib, path.parent.name, path.stem)
    body = path.read_bytes()
    return bool(waves) and all(same_as_game(lib, b, i, body) for b, i in waves)


def export(lib: xsb.Library, bank: str, out, cues=None):
    """Write a sound bank's cues (all, or those named) as .wav files; returns
    the paths written."""
    sb = lib.sound_bank(bank)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    want = None if not cues else {c.lower() for c in cues}
    written = []
    for cue in sb.cues:
        if want is not None and cue.name.lower() not in want:
            continue
        for stem, (wbank, index) in file_names(sb, cue):
            path = out / f"{stem}.wav"
            path.write_bytes(decoded(lib, wbank, index))
            written.append(path)
    return written


def _library(data):
    if getattr(data, "sound_library", None) is None:
        data.sound_library = xsb.Library(data.dir)
        data.sound_waves = {}           # (wave bank, index) -> (.wav bytes, mod, file label)
        data.sound_new = {}             # sound bank -> {cue name lower: (name, .wav bytes, mod, file label)}
    return data.sound_library


def apply(data, folder, result):
    """sounds/<bank>/<cue>[.<n>].wav: queue wave replacements (written by finish())."""
    root = Path(folder) / "sounds"
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".wav") if root.is_dir() else []
    if not files:
        return
    lib = _library(data)
    for f in files:
        rel = f.relative_to(root)
        new = len(rel.parts) == 3 and rel.parts[1].lower() == "new"
        if len(rel.parts) != 2 and not new:
            raise ValueError(f"sounds/{rel.as_posix()}: put it in sounds/<sound bank>/<cue>.wav "
                             "(or sounds/<sound bank>/new/<cue>.wav for a new cue)")
        bank, label = rel.parts[0], f"sounds/{rel.as_posix()}"
        if bank.lower() not in lib.sound_paths:
            raise ValueError(f"{label}: the game has no sound bank {bank!r}")
        body = f.read_bytes()
        try:
            xwb.read_wav(body)
        except ValueError as err:
            raise ValueError(f"{label}: {err}") from err
        if new:
            _queue_new(data, lib, bank, f.stem, body, label, result)
            continue
        cue, waves = targets(lib, bank, f.stem)
        for key in waves:
            prev = data.sound_waves.get(key)
            if prev and prev[1] != data.project:
                data.conflicts.append(f"{data.project}'s {label} replaces a sound {prev[1]}'s {prev[2]} "
                                      f"also replaces; {prev[1]}'s is lost")
            data.sound_waves[key] = (body, data.project, label)
            others = [f"{b}.{c}" for b, c in lib.users(*key) if not (b == bank.lower() and c == cue.name)]
            if others:
                result.warnings.append(f"{label}: the same wave also plays for " + ", ".join(others[:6]) +
                                       (f" and {len(others) - 6} more" if len(others) > 6 else ""))
        result.changes.append(f"sound {bank}.{cue.name}: {len(waves)} wave{'s' if len(waves) != 1 else ''} "
                              f"replaced ({', '.join(f'{b} #{i}' for b, i in waves)})")


def _queue_new(data, lib, bank, name, body, label, result):
    if not name or len(name) > 60 or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"{label}: name new cues with letters, digits and _ (60 at most)")
    sb = lib.sound_bank(bank)
    if sb.cue(name):
        raise ValueError(f"{label}: {bank} already has a cue {sb.cue(name).name}; to replace it, "
                         f"put the file in sounds/{bank}/{sb.cue(name).name}.wav")
    queued = data.sound_new.setdefault(bank.lower(), {})
    prev = queued.get(name.lower())
    if prev and prev[2] != data.project:
        data.conflicts.append(f"{data.project}'s {label} adds cue {bank}.{name}, which {prev[2]}'s {prev[3]} "
                              f"also adds; {prev[2]}'s is lost")
    queued[name.lower()] = (name, body, data.project, label)
    result.changes.append(f"sound {bank}.{name}: new cue (play it with Audio_PlayVoiceover('{bank}.{name}') "
                          "or as any other cue)")


def _entry_name(body):
    info = next((b for t, b in ucfb.iter_chunks(body) if t == b"INFO"), b"")
    return info.split(b"\0")[1].decode("latin-1") if info.startswith(b"name\0") else ""


def _template(lib, bank, kids):
    """A simple cue of the bank with plain registry entries, to copy settings from."""
    sb = lib.sound_bank(bank)
    sounds = {_entry_name(b).lower() for t, b in kids if t == b"xsl_"}
    for c in sb.cues:
        if c.sound < len(sb.sounds) and sb.sounds[c.sound].kind == "simple" and \
                f"{bank}.{c.name}".lower() in sounds and sb.sounds[c.sound].waves:
            return c
    raise ValueError(f"sound bank {bank} has no simple cue to base new cues on")


def _registry(data, bank):
    """(archive, record, children, padding) of the assets.dsk record listing the bank's cues."""
    prefix = bank.lower() + "."
    for a, r in data.records(REGISTRY, ("assets.dsk",)):
        body = data.current(a, r)
        roots, outer = ucfb.split(body)
        kids, inner = ucfb.split(roots[0][1])
        if any(t == b"xcl_" and _entry_name(b).lower().startswith(prefix) for t, b in kids):
            return a, r, kids, (outer, inner)
    raise ValueError(f"no assets.dsk record lists sound bank {bank}'s cues")


def _copy_entry(kids, tag, old, new, wave=None):
    """A copy of a registry chunk renamed from old to new (and, for a sound,
    playing `wave` = (wave bank, index))."""
    body = next((b for t, b in kids if t == tag and _entry_name(b).lower() == old.lower()), None)
    if body is None:
        raise ValueError(f"no {tag.decode()} entry for {old}")
    tree = ucfb.parse(ucfb.pack_chunk(tag, body))[0]

    def ser(c, parent, edit):
        if c.children:
            return ucfb.pack_chunk(c.tag, b"".join(ser(k, c.tag, edit) for k in c.children))
        return ucfb.pack_chunk(c.tag, edit(c.data, parent))

    if ser(tree, None, lambda d, p: d) != ucfb.pack_chunk(tag, body):
        raise ValueError(f"the {tag.decode()} entry for {old} doesn't rebuild exactly")
    old_b, new_b = old.encode("latin-1"), new.encode("latin-1")

    def edit(d, parent):
        if parent == b"WAVE" and wave:
            return b"name\0%s.%s\0bank\0%s\0wave_entry_index\0%d\0" % (
                wave[0].encode(), new_b.split(b".", 1)[1], wave[0].encode(), wave[1])
        return d.replace(b"name\0" + old_b + b"\0", b"name\0" + new_b + b"\0")

    return ucfb.split(ser(tree, None, edit))[0][0]


def finish(data, result):
    """Write every sound change into data.files: new cues into their sound
    banks and the registry, and wave banks with replaced or added waves."""
    if getattr(data, "sound_library", None) is None:
        return
    lib = data.sound_library
    added = {}                  # wave bank -> [(format, data, name)]
    for bank, cues in sorted(data.sound_new.items()):
        sb = lib.sound_bank(bank)
        a, r, kids, pad = _registry(data, bank)
        template = _template(lib, bank, kids)
        wbank, windex = sb.sounds[template.sound].waves[0]
        e = lib.wave_bank(wbank).entries[windex]
        new_cues, new_kids = [], []
        for name, body, _, label in cues.values():
            try:
                wave = xwb.encode_like(e, body)
            except ValueError as err:
                raise ValueError(f"{label}: {err}") from err
            adds = added.setdefault(wbank.lower(), [])
            index = len(lib.wave_bank(wbank).entries) + len(adds)
            adds.append((e.format, wave, name))
            new_cues.append((name, index, sb.wave_banks.index(wbank), template.sound))
            full = f"{bank}.{name}"
            new_kids.append(_copy_entry(kids, b"xcl_", f"{bank}.{template.name}", full))
            new_kids.append(_copy_entry(kids, b"xsl_", f"{bank}.{template.name}", full, (wbank, index)))
            ch, rate, _ = xwb.read_wav(body)
            if (ch, rate) != (e.channels, e.rate):
                result.warnings.append(f"{label}: converted from {ch} channel{'s' if ch != 1 else ''} at {rate} Hz "
                                       f"to the bank's {e.channels} at {e.rate} Hz")
        path = lib.sound_paths[bank]
        data.files[lib.rel(path)] = xsb.add_cues(path.read_bytes(), new_cues)
        body = ucfb.pack_chunk(b"ucfb", ucfb.pack_children(kids + new_kids, pad[1]), pad[0])
        data.set(a, r, body)
        result.changes.append(f"sound bank {bank}: {len(new_cues)} new cue{'s' if len(new_cues) != 1 else ''} "
                              f"(settings from {template.name}), registered in {a}")
    by_bank = {}
    for (bank, index), (body, _, label) in data.sound_waves.items():
        by_bank.setdefault(bank.lower(), {})[index] = (body, label)
    for bank in sorted(set(by_bank) | set(added)):
        waves = by_bank.get(bank, {})
        wb = lib.wave_bank(bank)
        replace = {}
        for index, (body, label) in waves.items():
            e = wb.entries[index]
            if same_as_game(lib, bank, index, body):
                continue
            try:
                replace[index] = xwb.encode_like(e, body)
            except ValueError as err:
                raise ValueError(f"{label}: {err}") from err
            ch, rate, _ = xwb.read_wav(body)
            if (ch, rate) != (e.channels, e.rate):
                result.warnings.append(f"{label}: converted from {ch} channel{'s' if ch != 1 else ''} at {rate} Hz "
                                       f"to the game's {e.channels} at {e.rate} Hz")
        new = added.get(bank, [])
        if not replace and not new:
            result.changes.append(f"wave bank {bank}: unchanged (the sounds are the game's own)")
            continue
        data.files[lib.rel(lib.wave_paths[bank])] = xwb.rebuild(wb, replace, new)
        parts = [f"{len(replace)} wave{'s' if len(replace) != 1 else ''} replaced"] if replace else []
        parts += [f"{len(new)} wave{'s' if len(new) != 1 else ''} added"] if new else []
        result.changes.append(f"wave bank {bank}: " + ", ".join(parts))
