"""Faction re-skins: a faction's names in the game's text.

The game has eight faction slots fixed in its code (Faction_FromHash): a
mod can't add a faction, but it can give the four employers and North
Korea ("nk") new names. factions.json:

    {"mafia": {"name": "Yakuza", "adjective": "Yakuza", "short": "Yakuza"},
     "everywhere": false}

- "name": the faction's full name ("Russian Mafia", "China", "South Korea",
  "Allied Nations" / "Allies");
- "adjective": as in "Chinese Officer", "Allied HQ" (default: the name);
- "short": the short form ("Mafia", "SK", "AN"; default: the name);
- "people": as in "Witnessed by South Koreans" (default: the adjective);
- "army" (nk only): the Korean People's Army, "KPA Soldier" (default: the
  adjective). North Korea's old forms are North Korea and DPRK (name), North
  Korean (adjective), North Koreans (people), NK (short).

By default only the interface's faction strings change: the PDA and
contracts menu names, the trespassing and disguise messages, mission info,
HQ labels, the race challenges' titles. With "everywhere": true every
English string does (mission text, mail, objectives), but not voiced lines'
subtitles ("vo.*": the voice still says the old name) unless "subtitles":
true. Each old form is replaced whole-word, keeping its case style (CHINA ->
YAKUZA), and "a"/"an" before a new adjective follow its first letter. The
HUD's faction flags ([flagmafia]) are sprites and stay.

A faction's look: "looks" gives its people other models,

    {"china": {"looks": {"china_hum_soldier": "sk_hum_soldier",
                         "china_hum_officer": "my_officer"}}}

each the game's or a mod's new model (models/<name>.new.json). The build
puts the new model's geometry under the old model's name, so every
template, squad member and placed character that draws it changes, and
every load list that loads it loads the new model's textures too. A model
two factions share (the Mafia's allies_hum_mgsoldier) changes for both.
Vehicles too: a vehicle's model can stand in for another's when it has
every working joint the other has (seats, doors, wheels, guns, rotors,
treads: WORKING_JOINTS), and its ruin and wheel models go with it.
The factions' characters share a skeleton (bone_* joints), so one's
animations play on another's model, as retail's mixed models show.
"""
import re

FACTIONS = ("allies", "china", "mafia", "sk", "nk")
# each faction's names in the game's text, longest first: (form, which new name)
OLD = {
    "allies": [("Allied Nations", "name"), ("Allies", "name"), ("Allied", "adjective"), ("AN", "short")],
    "china": [("China", "name"), ("Chinese", "adjective")],
    "mafia": [("Russian Mafia", "name"), ("Mafia", "short")],
    "sk": [("South Koreans", "people"), ("South Korean", "adjective"), ("South Korea", "name"), ("SK", "short")],
    # North Korea, the enemy: also DPRK (the name) and its army, the Korean
    # People's Army / KPA ("KPA Soldier"), which take "army" (default: the
    # adjective)
    "nk": [("Korean People's Army", "army"), ("North Koreans", "people"), ("North Korean", "adjective"),
           ("North Korea", "name"), ("DPRK", "name"), ("KPA", "army"), ("NK", "short")],
}
# acronyms match in capitals only ("an Allied" isn't AN)
ACRONYMS = {"AN", "SK", "NK", "KPA", "DPRK"}


# A vehicle's working joints: what its template's seats, doors, wheels,
# guns, rotors and treads are attached to. A model can stand in for another
# only if it has every one of these the other has.
WORKING_JOINTS = ("wheel_", "turret_", "barrel_", "hp_barreltip", "hp_seat_", "hp_dock_", "rotor", "tailrotor",
                  "hp_tread", "k_tail", "hp_ropeattachment", "hp_connectionpoint")


def working_joints(joint_names):
    """The working joints (lower case) among a model's joint names."""
    return {j.lower() for j in joint_names if j.lower().startswith(WORKING_JOINTS)}


def missing_joints(old_joints, new_joints):
    """The old model's working joints the new one lacks (sorted)."""
    return sorted(working_joints(old_joints) - working_joints(new_joints))


def looks(doc):
    """[(faction, old model, new model)] from factions.json's "looks"."""
    out = []
    for f in FACTIONS:
        spec = doc.get(f) or {}
        for old, new in (spec.get("looks") or {}).items():
            old, new = str(old).strip(), str(new).strip()
            if not old or not new:
                raise ValueError(f"factions.json: {f}: \"looks\" maps a model to a model")
            if old.lower() != new.lower():
                out.append((f, old, new))
    return out


def interface_key(key, faction):
    """Whether a string key is one of the faction's interface strings."""
    k = key.lower()
    f = faction
    pats = [rf"interface\.datapod\.(standardcase\.)?{f}", rf"interface\.shell\.contractsmenu\.{f}",
            rf"missioninfo\.faction\.{f}", rf"faction\.{f}", rf"interface\.factionboundary\.{f}",
            rf"interface\.hud\.disguise\.(.+\.)?{f}", rf"(nw|sw)\.{f}hq\..+",
            r"challenge\..*race.*\.(title|description)", r"challenge\.(sw|nw)_challenge_race_.+"]
    if f == "mafia":
        pats.append(r"interface\.shell\.contractsmenu\.russianmafia")
    return any(re.fullmatch(p, k) for p in pats)


def names_of(spec):
    name = str(spec.get("name") or "").strip()
    if not name:
        return None
    adjective = str(spec.get("adjective") or name).strip()
    return {"name": name, "adjective": adjective, "short": str(spec.get("short") or name).strip(),
            "people": str(spec.get("people") or adjective).strip(),
            "army": str(spec.get("army") or adjective).strip()}


def rename(text, faction, names):
    """The text with a faction's old names replaced by its new ones."""
    out = text
    for old, which in OLD[faction]:
        new = names[which]
        if old in ACRONYMS:
            out = re.sub(rf"\b{re.escape(old)}\b", new.upper() if len(new) <= 3 else new, out)
            continue

        def repl(m, new=new):
            got = m.group(0)
            return new.upper() if got.isupper() and len(got) > 1 else new
        out = re.sub(rf"\b{re.escape(old)}\b", repl, out, flags=re.IGNORECASE)
    if out != text:
        # "a"/"an" before a new name follow its first letter
        for which in ("adjective", "name", "short", "people", "army"):
            new = names[which]
            art = "an" if new[:1].lower() in "aeiou" else "a"
            out = re.sub(rf"\b([Aa])n? (?={re.escape(new)}\b)",
                         lambda m: (art.capitalize() if m.group(1) == "A" else art) + " ", out)
    return out


def _names_only(doc):
    """factions.json without its "looks" (a faction with only looks drops out)."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk != "looks"}
            if not v:
                continue
        out[k] = v
    return out


def plan(strings, doc):
    """{key: new text} for factions.json (doc) over the game's strings
    ({key: text}), and the faction names it changed."""
    doc = _names_only(doc)          # ("looks" are models: the build swaps those)
    edits = {}
    everywhere = bool(doc.get("everywhere"))
    subtitles = bool(doc.get("subtitles"))
    for faction in FACTIONS:
        spec = doc.get(faction)
        if spec is None:
            continue
        if not isinstance(spec, dict):
            raise ValueError(f"factions.json: {faction!r} is an object of names")
        names = names_of(spec)
        if names is None:
            raise ValueError(f"factions.json: {faction} needs a \"name\"")
        for key, text in strings.items():
            if not everywhere and not interface_key(key, faction):
                continue
            if key.lower().startswith("vo.") and not subtitles:
                continue
            new = rename(edits.get(key, text), faction, names)
            if new != edits.get(key, text):
                edits[key] = new
    for k in doc:
        if k not in FACTIONS and k not in ("everywhere", "subtitles", "map"):
            raise ValueError(f"factions.json: {k!r} isn't a faction the game lets a mod rename "
                             f"({', '.join(FACTIONS)}; its 8 faction slots are fixed)")
    return edits
