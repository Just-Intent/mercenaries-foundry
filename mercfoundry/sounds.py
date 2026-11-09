"""Game sounds for mods: export cues as .wav, replace them from a mod.

A mod replaces sounds with .wav files named after the cue the game plays:

    sounds/<sound bank>/<cue>.wav       every wave the cue can play
    sounds/<sound bank>/<cue>.<n>.wav   only its n-th wave (cues that pick
                                        one of several at random, or play
                                        several in turn)

e.g. sounds/fio/SWNfio01.wav for Audio_PlayVoiceover('fio.SWNfio01'). The
build converts each .wav to the replaced wave's own format (Xbox ADPCM,
its channel count and sample rate), rebuilds that wave bank from the
player's copy and writes it to the mod as a loose DATAxbox/SOUND file.
`sound export` writes cues under the same names, so an exported file put
back in place round-trips.
"""
from pathlib import Path

from . import xsb, xwb


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
        if len(rel.parts) != 2:
            raise ValueError(f"sounds/{rel.as_posix()}: put it in sounds/<sound bank>/<cue>.wav")
        bank, label = rel.parts[0], f"sounds/{rel.as_posix()}"
        if bank.lower() not in lib.sound_paths:
            raise ValueError(f"{label}: the game has no sound bank {bank!r}")
        cue, waves = targets(lib, bank, f.stem)
        body = f.read_bytes()
        try:
            xwb.read_wav(body)
        except ValueError as err:
            raise ValueError(f"{label}: {err}") from err
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


def finish(data, result):
    """Rebuild every wave bank with replaced waves into data.files."""
    queued = getattr(data, "sound_waves", None)
    if not queued:
        return
    lib = data.sound_library
    by_bank = {}
    for (bank, index), (body, _, label) in queued.items():
        by_bank.setdefault(bank.lower(), {})[index] = (body, label)
    for bank, waves in sorted(by_bank.items()):
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
        if not replace:
            result.changes.append(f"wave bank {bank}: unchanged (the sounds are the game's own)")
            continue
        data.files[lib.rel(lib.wave_paths[bank])] = xwb.rebuild(wb, replace)
        result.changes.append(f"wave bank {bank}: {len(replace)} wave{'s' if len(replace) != 1 else ''} rebuilt")
