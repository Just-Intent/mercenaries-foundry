# Sound

The game's audio is XACT banks for the original Xbox in `DATAxbox/SOUND`:
wave banks (`.xwb`, the audio) and sound banks (`.xsb`, the cues the game
plays). `rsm_xbox.ini`, beside `DATAxbox`, registers the banks the game loads.
Wave banks are resident (loaded whole: effects, weapons, vehicles) or
streamed (`vo.xwb` holds all 4,873 voice lines, plus `music.xwb` and
`ambient.xwb`). 100 sound banks are registered, holding 5,252 cues.

Scripts and the engine play a cue by sound bank and name, such as
`Audio_PlayVoiceover('fio.SWNfio01')`: Fiona's first line in the opening
mission.

## Listing and exporting

```bash
python -m mercfoundry sound list game_files/DATAxbox            # the sound banks
python -m mercfoundry sound list game_files/DATAxbox fio        # fio's cues and their waves
python -m mercfoundry sound export game_files/DATAxbox fio SWNfio01 -o sounds
```

`sound list <bank>` shows each cue's kind and waves:

```
simple    fio.SWNFIO01: vo #3047 4.7s
variation fio.NOMONEY: vo #1014 1.0s; vo #1015 1.2s; vo #1016 0.9s
simple    w_ak47.wpn_ak47_clip: w_ak47 #1 0.2s wpn_ak47_clip
```

- A **simple** cue plays one wave.
- A **variation** cue picks one of several waves at random.
- A **tracks** cue plays waves in sequence or layered, with pitch and volume
  changes.

Resident banks also name their waves (the last column).

`sound export` writes 16-bit PCM `.wav` files at the game's own rate (22,050
Hz) into `<out>/<bank>/`. A cue with one wave is written as `<cue>.wav`. A cue
with several waves is written as `<cue>.1.wav`, `<cue>.2.wav` and so on. Leave
out the cue names to export the whole bank.

## Replacing sounds in a mod

| Put this in your mod folder | To |
|---|---|
| `sounds/<bank>/<cue>.wav` | Replace every wave the cue can play |
| `sounds/<bank>/<cue>.<n>.wav` | Replace only the cue's n-th wave (as `sound export` names them) |

The `.wav` must be PCM, 16-bit, at any rate, mono or stereo. The build converts
it to the replaced wave's own format: Xbox ADPCM, with that wave's channel
count and sample rate. It rebuilds the wave bank from your copy of the game and
writes the bank into the mod as a loose file, such as
`DATAxbox/SOUND/vo/vo.xwb`. The new sound can be longer or shorter than the
original.

The build warns in three cases:

- The replaced wave is shared with other cues. Many voice lines and effects
  are reused.
- The `.wav` had to be converted.
- Two mods replace the same wave. The later one in load order wins.

Some limits:

- A replaced voice line makes the mod's `vo.xwb` a full copy of the bank, about
  180 MB. It's built on the player's PC, so the shared mod only carries the
  `.wav`.
- A wave that looped over its whole length still loops. Other loop points are
  dropped.
- Cues can't be added yet. That needs a sound bank writer and registering the
  bank in `rsm_xbox.ini`.

`mod pack` drops `.wav` files that are unchanged exports of the game's audio.
Every other sound is shared as it is, so it has to be the author's own
recording, not edited game audio.

## Wave bank format (`.xwb`, version 3)

Read on every retail bank. A rebuild with nothing replaced is byte-for-byte
identical for all of them.

| Offset | Contents |
|---|---|
| 0x00 | `"WBND"`, u32 version (3) |
| 0x08 | 4 regions, (u32 offset, u32 length) each: bank data, entries, wave names, wave data |

**Bank data** holds these fields in order:

- u32 flags: bit 0 means streamed, bit 16 means the bank has wave names.
- u32 entry count.
- char name[16].
- u32 entry size (24).
- u32 name size (64).
- u32 alignment: 4 for resident banks, 2048 for streamed ones.
- u32 compact format.

**Entry** (24 bytes) holds these fields in order:

- u32 flags (0x10000).
- u32 format.
- u32 offset into the wave data.
- u32 length.
- u32 loop start.
- u32 loop length.

**Format** is a packed word:

| Bits | Meaning |
|---|---|
| 0–1 | Codec: 0 is PCM, 1 is Xbox ADPCM |
| 2–4 | Channels |
| 5–22 | Sample rate |
| 31 | Set for 16-bit samples |

**Xbox ADPCM** is IMA ADPCM in blocks of 36 bytes per channel:

- Each block starts with a 4-byte header: s16 first sample, u8 step index, u8 0.
- 32 bytes of 4-bit codes follow, giving 64 samples: the header's sample, then
  63 codes. The last code is unused.
- In stereo, the two headers come first, then the codes alternate in 4-byte
  runs per channel.

## Sound bank format (`.xsb`, `"SDBK"` version 11)

Worked out from the retail banks. Every reference in the 100 registered banks
resolves to a wave that exists, except two in `a_strike` that name a wave bank
one past the end (in the retail data itself). Cue-to-wave links were checked
against resident banks' wave names: `vh_jeep.engine_start` plays
`veh_jeep_engine_st`, and `hud.email_new` plays `ui_email_alert`.

| Offset | Contents |
|---|---|
| 0x00 | `"SDBK"`, u16 version (11), u16 crc |
| 0x08 | u32 wave bank names (16 bytes each), u32 track table, u32 variation and 3D data, u32 end |
| 0x18 | u16 ?, u16 ?, u16 sound count, u16 cue count, u16 ?, u16 wave bank count |
| 0x38 | Cues, 20 bytes each, then sounds, 20 bytes each |

**Cue** (20 bytes) holds these fields in order:

- u16 flags.
- u16 sound index (0xFFFF means none).
- u32 name offset.
- u32 −1.
- u32 ?.
- u32 0x07FFFFFF.

**Sound** (20 bytes) holds these fields in order:

- u32 `what`.
- u16 category or volume.
- u16 ?.
- u8 track count.
- u8 ?, u8 ?.
- u8 kind flags.
- 8 bytes, unknown.

The kind flags say what `what` means:

| Kind flags | `what` |
|---|---|
| Bit 3 set (simple) | u16 wave + u16 wave bank index |
| Bit 4 set (variation) | Offset of a variation table |
| Neither | Offset of `track count` tracks |

**Variation table:**

- u16 count: the low 12 bits; the rest are flags.
- u16 holding twice the count.
- `count` entries of (u16 wave, u16 wave bank, u16 weight from, u16 weight to).
  Weights run to 10,000.

**Track:** u8 event count, u24 offset of its events.

**Event:** u8 type, u24 time in ms, then a body that depends on the type:

| Type | Size | Body |
|---|---|---|
| 0 | 12 | Play wave: u32 flags, u16 wave + u16 wave bank. With flags bit 10 set, a u32 offset of a variation table instead. |
| 1 | 24 | Play wave with pitch and volume variation. Starts like type 0. |
| 3 | 8 | Loop or end; always the track's last event |
| 4, 5 | 16 | Pitch and volume |
| 12, 15 | 8 | Unknown |
| 8, 10, 11, 16 | 8 + n | u32 length n, then n bytes |

Wave bank names resolve through `rsm_xbox.ini`. Every voice sound bank names
`vo`, the shared streamed bank. The per-speaker wave banks in `SOUND/vo/<speaker>/`
(`fio.xwb` and others) aren't registered, so the game doesn't use them.
