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
import json
import re
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
    likes = sorted(root.glob("*/new/*.json")) if root.is_dir() else []
    if not files and not likes:
        return
    lib = _library(data)
    # sounds/<bank>/new/<cue>.json {"like": <a cue of the bank>}: a copy of
    # that cue as it is (all its tracks and settings), its own waves from
    # <cue>.<n>.wav (or <cue>.wav for every one), the rest the cue's
    copies = {}
    for j in likes:
        bank, label = j.parent.parent.name, f"sounds/{j.relative_to(root).as_posix()}"
        if bank.lower() not in lib.sound_paths:
            raise ValueError(f"{label}: the game has no sound bank {bank!r}")
        try:
            like = str(json.loads(j.read_text(encoding="utf-8")).get("like") or "")
        except (ValueError, AttributeError):
            raise ValueError(f"{label}: give {{\"like\": \"<a cue of {bank}>\"}}") from None
        sb = lib.sound_bank(bank)
        cue = sb.cue(like)
        if cue is None:
            raise ValueError(f"{label}: {bank} has no cue {like!r} to copy")
        copies[(bank.lower(), j.stem.lower())] = _queue_like(data, lib, bank, j.stem, cue, label, result)
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
            stem = f.stem
            base, _, n = stem.rpartition(".")
            copy_ = copies.get((bank.lower(), stem.lower())) or (
                copies.get((bank.lower(), base.lower())) if base and n.isdigit() else None)
            if copy_ is not None:
                waves = copy_["waves"]
                which = range(len(waves)) if stem.lower() == copy_["name"].lower() else [int(n) - 1]
                if not all(0 <= i < len(waves) for i in which):
                    raise ValueError(f"{label}: {bank}.{copy_['like']} plays {len(waves)} "
                                     f"wave{'s' if len(waves) != 1 else ''}, not {n}")
                for i in which:
                    copy_["files"][i] = (body, label)
                continue
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


def _queue_like(data, lib, bank, name, cue, label, result):
    """A copy of a cue under a new name (queued; written by finish())."""
    if not name or len(name) > 60 or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"{label}: name new cues with letters, digits and _ (60 at most)")
    sb = lib.sound_bank(bank)
    if sb.cue(name):
        raise ValueError(f"{label}: {bank} already has a cue {sb.cue(name).name}")
    if cue.sound >= len(sb.sounds) or sb.sounds[cue.sound].kind == "variation":
        raise ValueError(f"{label}: {bank}.{cue.name} picks a wave at random; such a cue can't be copied yet")
    queued = data.sound_new.setdefault(bank.lower(), {})
    prev = queued.get(name.lower())
    if prev and prev[2] != data.project:
        data.conflicts.append(f"{data.project}'s {label} adds cue {bank}.{name}, which {prev[2]}'s {prev[3]} "
                              f"also adds; {prev[2]}'s is lost")
    copy_ = {"name": name, "like": cue.name, "cue": cue, "waves": sb.cue_waves(cue), "files": {}}
    queued[name.lower()] = (name, copy_, data.project, label)
    result.changes.append(f"sound {bank}.{name}: new cue, a copy of {bank}.{cue.name} (name it where the game "
                          f"names a cue: an effects entry's \"rotor\", Audio_PlaySound...)")
    return copy_


def _entry_name(body):
    info = next((b for t, b in ucfb.iter_chunks(body) if t == b"INFO"), b"")
    return info.split(b"\0")[1].decode("latin-1") if info.startswith(b"name\0") else ""


def _template(lib, bank, kids):
    """(cue, registry tag): the bank's first simple cue with plain registry
    entries, to copy settings from. Its sound entry is xsl_ for a sound that
    plays the same everywhere (positional 0) or xsh_ for a 3D one (positional
    1, with minimum_distance and rolloff_factor: it fades with distance)."""
    sb = lib.sound_bank(bank)
    sounds = {_entry_name(b).lower(): t for t, b in kids if t in (b"xsl_", b"xsh_")}
    for c in sb.cues:
        tag = sounds.get(f"{bank}.{c.name}".lower())
        if tag and c.sound < len(sb.sounds) and sb.sounds[c.sound].kind == "simple" and sb.sounds[c.sound].waves:
            return c, tag, f"{bank}.{c.name}"
    # Banks of streamed loops (ambient): every cue is a tracks sound that
    # plays one wave, looped; its cue entry names a sound entry of its own.
    # Prefer a plain loop (loop_count -1, one component: no volume curves,
    # as ambient.city_dense_dist_war) over a one-off like intro_jet.
    plain = []
    for c in sb.cues:
        if c.sound >= len(sb.sounds) or sb.sounds[c.sound].kind != "tracks" or len(sb.sounds[c.sound].waves) != 1:
            continue
        cue_entry = next((b for t, b in kids if t == b"xcl_" and _entry_name(b).lower() == f"{bank}.{c.name}".lower()),
                         None)
        ref = _sound_ref(cue_entry) if cue_entry else None
        body = next((b for t, b in kids if t in (b"xsl_", b"xsh_") and ref and _entry_name(b).lower() == ref.lower()),
                    b"")
        if b"loop_count\0-1\0" in body and b"num_components\x001\0" in body:
            plain.append(c)
    for c in plain + list(sb.cues):
        if c.sound >= len(sb.sounds) or sb.sounds[c.sound].kind != "tracks" or len(sb.sounds[c.sound].waves) != 1:
            continue
        cue_entry = next((b for t, b in kids if t == b"xcl_" and _entry_name(b).lower() == f"{bank}.{c.name}".lower()),
                         None)
        ref = _sound_ref(cue_entry) if cue_entry else None
        if ref and ref.lower() in sounds:
            return c, sounds[ref.lower()], ref
    raise ValueError(f"sound bank {bank} has no cue to base new cues on")


def _sound_ref(cue_entry):
    """The sound entry a cue entry (xcl_) plays: its SNDS REC_ name."""
    m = re.search(rb"SNDS.{4}REC_.{4}name\0([^\0]+)\0", cue_entry, re.S)
    return m.group(1).decode("latin-1") if m else None


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


def _copy_entry(kids, tag, old, new, wave=None, also=()):
    """A copy of a registry chunk renamed from old to new (and, for a sound,
    playing `wave` = (wave bank, index)); `also`: more (old, new) names in
    it to rename (a cue entry's sound entry)."""
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
        if parent == b"WAVE" and isinstance(wave, dict):
            # a copied sound: {(wave bank, old index): (new index, wave name)}
            m = re.match(rb"name\0[^\0]*\0bank\0([^\0]+)\0wave_entry_index\0(\d+)\0", d)
            hit = m and wave.get((m.group(1).decode("latin-1").lower(), int(m.group(2))))
            if hit:
                return b"name\0%s\0bank\0%s\0wave_entry_index\0%d\0" % (
                    hit[1].encode("latin-1"), m.group(1), hit[0]) + d[m.end():]
            return d
        if parent == b"WAVE" and wave:
            return b"name\0%s.%s\0bank\0%s\0wave_entry_index\0%d\0" % (
                wave[0].encode(), new_b.split(b".", 1)[1], wave[0].encode(), wave[1])
        d = d.replace(b"name\0" + old_b + b"\0", b"name\0" + new_b + b"\0")
        for o, n in also:
            d = d.replace(b"name\0" + o.encode("latin-1") + b"\0", b"name\0" + n.encode("latin-1") + b"\0")
        return d

    return ucfb.split(ser(tree, None, edit))[0][0]


def _add_copy(lib, data, bank, sb, kids, copy_, label, added, new_cues, new_kids, result):
    """A copied cue (new/<cue>.json "like"): its waves appended to their wave
    banks, its sound a copy of the cue's (every track) playing them, and its
    registry entries copies of the cue's (xcl_, and the sound entry it names,
    xsl_/xsh_, with those waves' WAVE records)."""
    cue, name = copy_["cue"], copy_["name"]
    remap, reg_map = {}, {}
    for i, (body, flabel) in sorted(copy_["files"].items()):
        wbank, old = copy_["waves"][i]
        e = lib.wave_bank(wbank).entries[old]
        try:
            wave = xwb.encode_like(e, body)
        except ValueError as err:
            raise ValueError(f"{flabel}: {err}") from err
        adds = added.setdefault(wbank.lower(), [])
        index = len(lib.wave_bank(wbank).entries) + len(adds)
        wname = f"{name}_{i + 1}"
        adds.append((e.format, wave, wname, e.flags))
        remap[(old, sb.wave_banks.index(wbank))] = index
        reg_map[(wbank.lower(), old)] = (index, f"{bank}.{wname}")
        ch, rate, _ = xwb.read_wav(body)
        if (ch, rate) != (e.channels, e.rate):
            result.warnings.append(f"{flabel}: converted from {ch} channel{'s' if ch != 1 else ''} at {rate} Hz "
                                   f"to the game's {e.channels} at {e.rate} Hz")
    new_cues.append((name, remap, None, cue.sound))
    cue_entry, full = f"{bank}.{cue.name}", f"{bank}.{name}"
    body = next((b for t, b in kids if t == b"xcl_" and _entry_name(b).lower() == cue_entry.lower()), None)
    if body is None:
        raise ValueError(f"{label}: the game registers no cue entry for {cue_entry}")
    sound_entry = _sound_ref(body)
    tag = next((t for t, b in kids if t in (b"xsl_", b"xsh_") and sound_entry
                and _entry_name(b).lower() == sound_entry.lower()), None)
    if tag is None:
        raise ValueError(f"{label}: no sound entry for {cue_entry} ({sound_entry})")
    other = [(sound_entry, full)] if sound_entry.lower() != cue_entry.lower() else []
    new_kids.append(_copy_entry(kids, b"xcl_", cue_entry, full, also=other))
    new_kids.append(_copy_entry(kids, tag, sound_entry, full, reg_map))
    shared = len(copy_["waves"]) - len(copy_["files"])
    result.changes.append(f"sound {bank}.{name}: {len(copy_['files'])} of {bank}.{cue.name}'s "
                          f"{len(copy_['waves'])} waves its own" + (f", {shared} shared with it" if shared else ""))


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
        simple = any(not isinstance(body, dict) for _, body, _, _ in cues.values())
        if simple:
            template, tag, sound_entry = _template(lib, bank, kids)
            wbank, windex = sb.sounds[template.sound].waves[0]
            e = lib.wave_bank(wbank).entries[windex]
            cue_entry = f"{bank}.{template.name}"
        new_cues, new_kids = [], []
        for name, body, _, label in cues.values():
            if isinstance(body, dict):
                _add_copy(lib, data, bank, sb, kids, body, label, added, new_cues, new_kids, result)
                continue
            try:
                wave = xwb.encode_like(e, body)
            except ValueError as err:
                raise ValueError(f"{label}: {err}") from err
            adds = added.setdefault(wbank.lower(), [])
            index = len(lib.wave_bank(wbank).entries) + len(adds)
            adds.append((e.format, wave, name, e.flags))
            new_cues.append((name, index, sb.wave_banks.index(wbank), template.sound))
            full = f"{bank}.{name}"
            other = [(sound_entry, full)] if sound_entry.lower() != cue_entry.lower() else []
            new_kids.append(_copy_entry(kids, b"xcl_", cue_entry, full, also=other))
            new_kids.append(_copy_entry(kids, tag, sound_entry, full, (wbank, index)))
            ch, rate, _ = xwb.read_wav(body)
            if (ch, rate) != (e.channels, e.rate):
                result.warnings.append(f"{label}: converted from {ch} channel{'s' if ch != 1 else ''} at {rate} Hz "
                                       f"to the bank's {e.channels} at {e.rate} Hz")
        path = lib.sound_paths[bank]
        data.files[lib.rel(path)] = xsb.add_cues(path.read_bytes(), new_cues)
        body = ucfb.pack_chunk(b"ucfb", ucfb.pack_children(kids + new_kids, pad[1]), pad[0])
        data.set(a, r, body)
        if not simple:
            result.changes.append(f"sound bank {bank}: {len(new_cues)} new cue{'s' if len(new_cues) != 1 else ''} "
                                  f"(copies), registered in {a}")
            continue
        entry = next(b for t, b in kids if t == tag and _entry_name(b).lower() == sound_entry.lower())
        kind = sb.sounds[template.sound].kind
        result.changes.append(f"sound bank {bank}: {len(new_cues)} new cue{'s' if len(new_cues) != 1 else ''} "
                              f"(settings from {template.name}"
                              + (", 3D: fades with distance"
                                 if tag == b"xsh_" and b"positional\x000\x00" not in entry else "")
                              + (", looped whole" if kind == "tracks" else "") + f"), registered in {a}")
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
