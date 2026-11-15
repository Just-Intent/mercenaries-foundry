"""Faction re-skins: a faction's names in the game's text.

The game has eight faction slots fixed in its code (Faction_FromHash): a
mod can't add a faction, but it can give one of the four employers new
names. factions.json:

    {"mafia": {"name": "Yakuza", "adjective": "Yakuza", "short": "Yakuza"},
     "everywhere": false}

- "name": the faction's full name ("Russian Mafia", "China", "South Korea",
  "Allied Nations" / "Allies");
- "adjective": as in "Chinese Officer", "Allied HQ" (default: the name);
- "short": the short form ("Mafia", "SK", "AN"; default: the name);
- "people": as in "Witnessed by South Koreans" (default: the adjective).

By default only the interface's faction strings change: the PDA and
contracts menu names, the trespassing and disguise messages, mission info,
HQ labels, the race challenges' titles. With "everywhere": true every
English string does (mission text, mail, objectives), but not voiced lines'
subtitles ("vo.*": the voice still says the old name) unless "subtitles":
true. Each old form is replaced whole-word, keeping its case style (CHINA ->
YAKUZA), and "a"/"an" before a new adjective follow its first letter. The
HUD's faction flags ([flagmafia]) are sprites and stay.
"""
import re

FACTIONS = ("allies", "china", "mafia", "sk")
# each faction's names in the game's text, longest first: (form, which new name)
OLD = {
    "allies": [("Allied Nations", "name"), ("Allies", "name"), ("Allied", "adjective"), ("AN", "short")],
    "china": [("China", "name"), ("Chinese", "adjective")],
    "mafia": [("Russian Mafia", "name"), ("Mafia", "short")],
    "sk": [("South Koreans", "people"), ("South Korean", "adjective"), ("South Korea", "name"), ("SK", "short")],
}
# acronyms match in capitals only ("an Allied" isn't AN)
ACRONYMS = {"AN", "SK"}


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
            "people": str(spec.get("people") or adjective).strip()}


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
        for which in ("adjective", "name", "short", "people"):
            new = names[which]
            art = "an" if new[:1].lower() in "aeiou" else "a"
            out = re.sub(rf"\b([Aa])n? (?={re.escape(new)}\b)",
                         lambda m: (art.capitalize() if m.group(1) == "A" else art) + " ", out)
    return out


def plan(strings, doc):
    """{key: new text} for factions.json (doc) over the game's strings
    ({key: text}), and the faction names it changed."""
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
        if k not in FACTIONS and k not in ("everywhere", "subtitles"):
            raise ValueError(f"factions.json: {k!r} isn't a faction the game lets a mod rename "
                             f"({', '.join(FACTIONS)}; its 8 faction slots are fixed)")
    return edits
