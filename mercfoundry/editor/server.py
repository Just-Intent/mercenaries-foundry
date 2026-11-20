"""The editor's local web server (127.0.0.1 only; standard library).

GET  /                         the editor page (static/)
GET  /vendor/...               three.js (vendor/)
GET  /api/info                 {"mod": name, "maps": [...]}
GET  /api/terrain/<map>.json   terrain size, spacing and origin
GET  /api/terrain/<map>.bin    float32 heights, row by row
GET  /api/look/<map>.json      the ground's texture layers (texture, scale, axis)
GET  /api/look/<map>.paint     each layer's opacity, layer after layer (bytes)
GET  /api/look/<map>.light     baked lighting, RGB per height sample
GET  /api/look/<map>.decals    road and concrete decals (cell, texture, corner UVs)
GET  /api/texture/<name>.png   a game texture
GET  /api/objects/<map>        the map's objects, with the mod's editor edits and placed objects
GET  /api/model/<name>.glb     a model with its textures
GET  /api/bounds/<map>         each drawn model's bounds, for far-away stand-ins
GET  /api/context-bounds/<map> the same for other mods' objects
GET  /api/layout/<map>         paths and zones: the game's (with the mod's layout
                               changes) and the mod's own (encounters/*.json)
POST /api/layout/save          {"map", "game": [...], "mod": {file: {...}}, "own"?: {zone: {...}}}
                               (own: the zones the mod adds to the map's own record: water)
GET  /api/context/<map>        objects other enabled mods add, move or remove
POST /api/live/poll            the running game's plugin: its state in, commands out
GET  /api/live/state           connected?, the player's position, command results
POST /api/live/send            {"commands": [...]} for the game (see live.py)
GET  /api/encounters/<map>     encounters: the game's (read only) and the mod's
GET  /api/sky/<map>            its base sky and atmosphere presets, and the mod's changes (sky/<map>.json)
POST /api/models/new           {"name", "base", "glb" (base64)}: a new object's model (models/<name>.glb + .new.json)
POST /api/sky/save             {"map", "doc", "lighting"?}: writes sky/<map>.json (and terrain/<map>.lighting.json; null removes it)
POST /api/lighting/preview     {"map", "lighting", "base"?}: the ground lit from that sun, as /api/look/<map>.light
GET  /api/animtables          animation tables, with the models of the characters using each
GET  /api/mapkeys/<map>       a new map's HQ, medevac and HUD art keys (content.py)
POST /api/mapkeys/save         {"map", "changes": {key: value | null}}, checked as the build does
GET  /api/deck                 the mod's deck/*.json, each with the build's check and its portraits
POST /api/deck/save            {"file", "doc"}; /api/deck/portrait {"suit", "card", "png" (base64)}
POST /api/texture/upload       {"name", "png"}: textures/<name>.png
GET  /api/modfile/<path>.png   one of the mod's own PNGs
POST /api/switch               back to the mod list
GET  /api/animtable/<name>    the game's table, the mod's version, its models, the flag meanings
GET  /api/clips               every clip name
GET  /api/clip/<model>/<clip>.glb  the model (no textures) carrying the clip, in place: the preview
POST /api/animtable/save      {"name", "doc"}: animtables/<name>.changes.json (or the whole table's file)
GET  /api/animupdate          xbox.ini's animation update rates by distance: the game's and the mod's
POST /api/animupdate/save     {"tables": {"AnimUpdateHigh": ["30 1 1", ...]}}: config/xbox.ini.json
GET  /api/mapsettings/<map>      a mod's new map: what it copies, arrival point, message, its layers
POST /api/maps/new               {"name", "from"}: a new map (maps/<name>.json) -> {"name"}
GET  /api/mods                  the game's source mods (folder, name, description, enabled, current)
POST /api/mods/switch          {"folder"}: edit and save to that mod
POST /api/mods/create          {"name", "description"?}: a new source mod (enabled), switched to
POST /api/mods/update          {"folder", "name"?, "description"?, "enabled"?}
POST /api/mods/delete          {"folder"}: moved to the game's foundry-trash folder, off the launcher's list
GET  /api/marks/<map>           travel pilots and arrival points the mod's maps/*.json put on this map
POST /api/marks/save            {"marks": [{kind, to, index, position, rotation}]}: moved marks
POST /api/maps/travel           {"op": "add"|"remove", "to", "on", "position"?, "heading"?, "index"?}: travel pilots
POST /api/maps/save             {"map", "doc"}: a new map's arrival point, message and loading layers
GET  /api/properties/<ref>     an object's properties: id:<layer>:<index>, or from:<name> / from:model:<model>
GET  /api/factions             the mod's factions.json (faction re-skins) and the game's names
POST /api/factions/preview     {"doc"}: what it would change ({count, samples})
POST /api/factions/save        {"doc"}: writes factions.json
GET  /api/music                the game's music tracks, for music lines
GET  /api/voices               the game's voiced lines with subtitles, for radio lines: [{cue, text}]
GET  /api/voice/<bank>.<cue>.wav  a voice line to listen to
GET  /api/templates            objects a squad member can copy (template_*)
GET  /api/groundtextures       every ground texture the game's maps paint with (texture, scale, axis, map)
POST /api/terrain/changelayer  {"map", "index", "texture"}: another texture for a ground layer
POST /api/terrain/removelayer  {"map", "index"}: take off a ground layer the mod added (and its paint)
POST /api/terrain/restorelayer {"map", "index", "layer", "trash"?}: undo removelayer (its paint back from .trash/)
POST /api/terrain/addlayer     {"map", "texture", "scale"?, "axis"?}: a new ground layer (terrain/<map>.layers.json)
GET  /api/crowns              models whose branches bullets hit (models/<model>.collision.json "crown")
POST /api/crown                {"model", "on"}: bullets hit that tree model's branches, or not
GET  /api/placeables           everything Place can copy, any map: templates, named objects, model:<model>
POST /api/encounters/save      {"map", "files": {file: [encounter, ...]}}
POST /api/placed/save          {"map", "files": {file: [{from, name, position, rotation}]}}: objects placed from templates
POST /api/terrain/save         {"map", "samples": [[column, row, height units], ...]}: painted ground heights
GET  /api/missions/<map>        the mod's missions on the map (+ their layer's squads and zones)
GET  /api/missions/check/<id>   what would stop the mission's script being written (the build's checks)
POST /api/missions/testmission {"map", "centre", "faction"?}: a feature test mission laid out there -> {"id"}
POST /api/missions/duplicate   {"id", "faction"?}: a copy at the next free number -> {"id", "notes"}
POST /api/missions/delete      {"id"}: its files go to the mod's .trash/ -> {"moved", "trash", "required_by"}
POST /api/missions/save        {"map", "mission": {faction, number, title, description, briefing, fee, objectives}}
POST /api/terrain/paint        {"map", "layers": {layer: {"texture", "pixels": [[col, row, opacity]]}}}
POST /api/terrain/roads        {"map", "roads": [{"texture", "span", "points": [[x, z], ...]}]}
POST /api/save                 {"moved": {...}, "copies": [...], "removed": [...]}
                               -> objects/editor.json
GET  /api/git                  the mod's own git repository: changed files (each marked if the
                               mod's tools write it: mod.json "generated"), branch, remotes
POST /api/git/commit           {"message", "paths": [...]}: those files committed
POST /api/git/push             push to its upstream (or its first remote, origin first)
GET  /api/build                the background build: running, step, lines, ok, zip
POST /api/build                {"playtest"?}: build the game's source mods as the launcher does
                               (mod sync), then a playtest zip (from a toolkit source tree)
"""
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import unquote, urlparse

from . import anims, content, gitpanel
from .live import Live
from .scene import Scene

TYPES = {".js": "text/javascript", ".html": "text/html; charset=utf-8", ".css": "text/css",
         ".json": "application/json", ".glb": "model/gltf-binary"}


def _static(path):
    """Bytes of a file under the editor package (works inside the .pyz)."""
    node = resources.files("mercfoundry.editor")
    for part in path.split("/"):
        if not part or part in (".", ".."):
            return None
        node = node / part
    try:
        return node.read_bytes() if node.is_file() else None
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def make_handler(scene: Scene, mod_name: str, live: Live = None):
    # One lock per kind of work, so a slow job (other mods' objects take ~20 s
    # the first time) doesn't hold up models, the ground or saving. Models and
    # textures need none: at worst one is built twice.
    objects_lock, terrain_lock, context_lock = threading.RLock(), threading.RLock(), threading.RLock()
    anims_lock = threading.RLock()       # the Animations panel (its first look reads every layer)
    git_lock = threading.Lock()          # the Changes panel's git commands, one at a time
    builds = gitpanel.Builds()
    live = live or Live()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def fail(self, code, message):
            self.send(code, {"error": message})

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    return self.send(200, _static("static/index.html"), TYPES[".html"])
                if path.startswith(("/static/", "/vendor/")):
                    data = _static(path.lstrip("/"))
                    if data is None:
                        return self.fail(404, "not found")
                    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
                    return self.send(200, data, TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream")
                if path == "/api/info":
                    with objects_lock:
                        from ..modbuild import project_name
                        return self.send(200, {"mod": project_name(scene.mod), "folder": scene.mod.name,
                                               "maps": scene.maps(),
                                               "can_switch": bool(getattr(self.server, "can_switch", False))})
                if path == "/api/mods":
                    with objects_lock:
                        return self.send(200, scene.mods())
                if path.startswith("/api/mapkeys/"):          # a new map's HQs, medevac, HUD art
                    return self.send(200, content.map_keys(scene.mod, path.rsplit("/", 1)[1]))
                if path == "/api/deck":
                    return self.send(200, content.deck_files(scene.mod))
                if path.startswith("/api/modfile/"):          # one of the mod's own PNGs
                    data = content.mod_png(scene.mod, path[len("/api/modfile/"):])
                    return self.send(200, data, "image/png") if data is not None else self.fail(404, "no such PNG")
                if path == "/api/git":
                    with git_lock:
                        st = gitpanel.status(scene.mod)
                    st["game"] = scene.game_dir() is not None
                    st["playtest"] = gitpanel.playtest_script() is not None
                    return self.send(200, st)
                if path == "/api/build":
                    return self.send(200, builds.state())
                if path.startswith("/api/missions/check/"):     # the build's checks for one mission
                    with objects_lock:
                        return self.send(200, scene.check_mission(path[len("/api/missions/check/"):]))
                if path.startswith("/api/missions/"):
                    with objects_lock:
                        return self.send(200, scene.missions(path[len("/api/missions/"):]))
                if path.startswith("/api/look/"):          # ground textures: layers, paint, light
                    name, _, part = path[len("/api/look/"):].partition(".")
                    with terrain_lock:
                        meta, paint, light = scene.terrain_look(name)
                    if part == "json":
                        return self.send(200, meta)
                    if part == "decals":
                        with terrain_lock:
                            return self.send(200, scene.terrain_decals(name))
                    return self.send(200, paint if part == "paint" else light, "application/octet-stream")
                if path.startswith("/api/texture/") and path.endswith(".png"):
                    data = scene.texture_png(path[len("/api/texture/"):-4])
                    return self.send(200, data, "image/png")
                if path.startswith("/api/terrain/"):
                    name = path[len("/api/terrain/"):]
                    with terrain_lock:
                        meta, heights = scene.terrain(name.rsplit(".", 1)[0])
                    if name.endswith(".json"):
                        return self.send(200, meta)
                    return self.send(200, heights, "application/octet-stream")
                if path.startswith("/api/objects/"):
                    with objects_lock:
                        objs = scene.objects(path[len("/api/objects/"):])
                        edits = scene.load_edits()
                        edits["placed"] = scene.load_placed(path[len("/api/objects/"):])
                        edits["layer_file"] = scene.editor_layer_file(path[len("/api/objects/"):])
                        edits["terrain_errors"] = getattr(scene, "terrain_errors", [])
                    return self.send(200, {"objects": objs, "edits": edits})
                if path.startswith("/api/bounds/"):
                    with objects_lock:
                        return self.send(200, scene.bounds(path[len("/api/bounds/"):]))
                if path.startswith("/api/context-bounds/"):
                    with context_lock:
                        return self.send(200, scene.bounds(path[len("/api/context-bounds/"):], context=True))
                if path == "/api/live/state":
                    return self.send(200, live.state())
                if path.startswith("/api/context/"):
                    with context_lock:
                        return self.send(200, scene.context_objects(path[len("/api/context/"):]))
                if path.startswith("/api/encounters/"):
                    with objects_lock:
                        return self.send(200, scene.encounters(path[len("/api/encounters/"):]))
                if path == "/api/animations":
                    with objects_lock:
                        return self.send(200, scene.animation_names())
                if path.startswith("/api/marks/"):
                    with objects_lock:
                        return self.send(200, scene.map_marks(path[len("/api/marks/"):]))
                if path.startswith("/api/mapsettings/"):
                    with objects_lock:
                        doc = scene.map_settings(path[len("/api/mapsettings/"):])
                        doc["game_maps"] = scene.game_maps()
                        return self.send(200, doc)
                if path.startswith("/api/sky/"):
                    with objects_lock:
                        return self.send(200, scene.sky(path[len("/api/sky/"):]))
                if path == "/api/animupdate":
                    with objects_lock:
                        return self.send(200, anims.update_rates(scene))
                if path == "/api/animtables":
                    with anims_lock:
                        return self.send(200, anims.tables(scene))
                if path.startswith("/api/animtable/"):
                    with anims_lock:
                        return self.send(200, anims.table(scene, path[len("/api/animtable/"):]))
                if path == "/api/clips":
                    with anims_lock:
                        return self.send(200, anims.clip_names(scene))
                if path.startswith("/api/clip/") and path.endswith(".glb"):
                    model_name, _, clip = path[len("/api/clip/"):-4].partition("/")
                    with anims_lock:
                        return self.send(200, anims.clip_glb(scene, model_name, clip), TYPES[".glb"])
                if path.startswith("/api/properties/"):
                    with objects_lock:
                        return self.send(200, scene.object_properties(path[len("/api/properties/"):]))
                if path == "/api/factions":
                    with objects_lock:
                        return self.send(200, scene.factions())
                if path == "/api/music":
                    return self.send(200, scene.music_tracks())
                if path == "/api/voices":
                    with objects_lock:
                        return self.send(200, scene.voice_lines())
                if path.startswith("/api/voice/") and path.endswith(".wav"):
                    return self.send(200, scene.voice_wav(path[len("/api/voice/"):-4]), "audio/wav")
                if path == "/api/groundtextures":
                    with objects_lock, terrain_lock:
                        return self.send(200, scene.ground_textures())
                if path == "/api/templates":
                    with objects_lock:
                        return self.send(200, scene.templates())
                if path == "/api/crowns":
                    with objects_lock:
                        return self.send(200, scene.bullet_crowns())
                if path == "/api/placeables":
                    with objects_lock:
                        return self.send(200, scene.placeables())
                if path.startswith("/api/layout/"):
                    with objects_lock:
                        return self.send(200, scene.layout(path[len("/api/layout/"):]))
                if path.startswith("/api/model/") and path.endswith(".glb"):
                    data = scene.model_glb(path[len("/api/model/"):-4])
                    return self.send(200, data, TYPES[".glb"])
                return self.fail(404, "not found")
            except KeyError as err:
                return self.fail(404, f"not found: {err}")
            except Exception as err:     # report to the page instead of dropping the connection
                return self.fail(500, f"{type(err).__name__}: {err}")

        def do_POST(self):
            path = urlparse(self.path).path
            if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                return self.fail(403, "cross-origin request refused")
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if path == "/api/live/poll":             # the game's plugin (text lines)
                return self.send(200, live.poll(body.decode("utf-8", "replace")), "text/plain; charset=utf-8")
            try:
                doc = json.loads(body or b"{}")
                # a change meant for the mod the page shows, not the one this server now saves to
                # (another tab switched mods, or the server was restarted on another)
                meant = self.headers.get("X-Foundry-Mod")
                if meant is not None and not path.startswith("/api/mods/") and path != "/api/live/send":
                    from urllib.parse import unquote
                    if unquote(meant) != scene.mod.name:
                        return self.fail(409, f"This page is editing mods/{unquote(meant)}, but the editor now saves to "
                                              f"mods/{scene.mod.name} (another editor tab switched it, or it was restarted). "
                                              f"Nothing was saved: reload the page, or switch back in Mods")
                if path == "/api/live/send":
                    return self.send(200, {"queued": live.send(doc.get("commands", []))})
                if path == "/api/git/commit":
                    with git_lock:
                        return self.send(200, gitpanel.commit(scene.mod, doc.get("message"), doc.get("paths")))
                if path == "/api/git/push":
                    with git_lock:
                        return self.send(200, gitpanel.push(scene.mod))
                if path == "/api/build":
                    return self.send(200, builds.start(scene.game_dir(), bool(doc.get("playtest"))))
                if path == "/api/mapkeys/save":
                    return self.send(200, content.save_map_keys(scene.mod, str(doc.get("map") or ""),
                                                                doc.get("changes") or {}))
                if path == "/api/deck/save":
                    return self.send(200, content.save_deck(scene.mod, str(doc.get("file") or ""), doc.get("doc") or {}))
                if path == "/api/deck/portrait":
                    return self.send(200, content.save_portrait(scene.mod, str(doc.get("suit") or ""),
                                                                str(doc.get("card") or ""), doc.get("png") or ""))
                if path == "/api/texture/upload":
                    return self.send(200, content.save_texture(scene.mod, str(doc.get("name") or ""), doc.get("png") or ""))
                if path == "/api/switch":             # back to the mod list (serve's loop)
                    if not getattr(self.server, "can_switch", False):
                        return self.fail(400, "started for one mod: restart the editor without a mod to choose")
                    self.server.switch = True
                    self.send(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return None
                if path == "/api/save":
                    with objects_lock:
                        n = scene.save_edits(doc.get("moved", {}), doc.get("copies", []), doc.get("removed", []),
                                             doc.get("map"))
                    return self.send(200, {"rules": n, "file": f"objects/{scene.rules_path().name}"})
                if path == "/api/placed/save":
                    with objects_lock:
                        return self.send(200, scene.save_placed(doc["map"], doc.get("files", {})))
                if path == "/api/missions/save":
                    with objects_lock:
                        return self.send(200, scene.save_mission(doc["map"], doc["mission"]))
                if path == "/api/models/new":
                    import base64
                    with objects_lock:
                        return self.send(200, scene.new_model(doc["name"], doc["base"], base64.b64decode(doc["glb"])))
                if path == "/api/factions/preview":
                    with objects_lock:
                        return self.send(200, scene.faction_preview(doc.get("doc") or {}))
                if path == "/api/factions/save":
                    with objects_lock:
                        return self.send(200, scene.save_factions(doc.get("doc") or {}))
                if path == "/api/maps/new":
                    with objects_lock:
                        return self.send(200, scene.new_map(doc["name"], doc.get("from") or "blank", doc.get("size")))
                if path.startswith("/api/mods/"):
                    # switching, making or deleting a mod changes what every
                    # other request reads: none run meanwhile
                    with objects_lock, terrain_lock, context_lock:
                        op = path[len("/api/mods/"):]
                        if op == "switch":
                            return self.send(200, scene.switch_mod(doc["folder"]))
                        if op == "create":
                            return self.send(200, scene.create_mod(doc["name"], doc.get("description") or ""))
                        if op == "update":
                            return self.send(200, scene.update_mod(doc["folder"], doc.get("name"),
                                                                   doc.get("description"), doc.get("enabled")))
                        if op == "delete":
                            return self.send(200, scene.delete_mod(doc["folder"]))
                        return self.fail(404, "not found")
                if path == "/api/crown":
                    with objects_lock:
                        return self.send(200, scene.bullet_crown(doc["model"], bool(doc.get("on"))))
                if path == "/api/marks/save":
                    with objects_lock:
                        return self.send(200, scene.save_map_marks(doc.get("marks") or []))
                if path == "/api/maps/travel":
                    with objects_lock:
                        if doc.get("op") == "remove":
                            return self.send(200, scene.remove_travel_pilot(doc["to"], doc["index"], doc["on"]))
                        return self.send(200, scene.add_travel_pilot(doc["to"], doc["on"], doc["position"],
                                                                     doc.get("heading") or 0))
                if path == "/api/maps/save":
                    with objects_lock:
                        return self.send(200, scene.save_map_settings(doc["map"], doc.get("doc") or {}))
                if path == "/api/lighting/preview":
                    with terrain_lock:
                        light, note = scene.lighting_preview(doc["map"], doc.get("lighting"), doc.get("base"))
                    return self.send(200, light, "application/octet-stream")
                if path == "/api/sky/save":
                    with objects_lock:
                        return self.send(200, scene.save_sky(doc["map"], doc.get("doc") or {}, doc.get("lighting", "keep")))
                if path == "/api/animupdate/save":
                    with objects_lock:
                        return self.send(200, anims.save_update_rates(scene, doc.get("tables") or {}))
                if path == "/api/animtable/save":
                    with anims_lock:
                        return self.send(200, anims.save_table(scene, doc["name"], doc["doc"]))
                if path == "/api/missions/testmission":
                    with objects_lock:
                        return self.send(200, scene.make_test_mission(doc["map"], doc["centre"], doc.get("faction") or "china",
                                                                     doc.get("which") or 1))
                if path == "/api/missions/duplicate":
                    with objects_lock:
                        return self.send(200, scene.duplicate_mission(doc["id"], doc.get("faction")))
                if path == "/api/missions/delete":
                    with objects_lock:
                        return self.send(200, scene.delete_mission(doc["id"]))
                if path == "/api/terrain/paint":
                    with objects_lock, terrain_lock:      # not while a ground read is building
                        return self.send(200, scene.save_paint(doc["map"], doc.get("layers", {})))
                if path == "/api/terrain/changelayer":
                    with objects_lock, terrain_lock:
                        return self.send(200, scene.change_ground_layer(doc["map"], doc["index"], doc["texture"]))
                if path == "/api/terrain/removelayer":
                    with objects_lock, terrain_lock:
                        return self.send(200, scene.remove_ground_layer(doc["map"], doc["index"]))
                if path == "/api/terrain/restorelayer":
                    with objects_lock, terrain_lock:
                        return self.send(200, scene.restore_ground_layer(doc["map"], doc["index"], doc["layer"],
                                                                         doc.get("trash")))
                if path == "/api/terrain/addlayer":
                    with objects_lock, terrain_lock:
                        return self.send(200, scene.add_ground_layer(doc["map"], doc["texture"],
                                                                     doc.get("scale"), doc.get("axis")))
                if path == "/api/terrain/roads":
                    with objects_lock, terrain_lock:      # not while a ground read is building
                        return self.send(200, scene.save_roads(doc["map"], doc.get("roads", [])))
                if path == "/api/terrain/save":
                    with objects_lock, terrain_lock:      # not while a ground read is building
                        return self.send(200, scene.save_terrain(doc["map"], doc.get("samples", [])))
                if path == "/api/encounters/save":
                    with objects_lock:
                        return self.send(200, scene.save_encounters(doc["map"], doc.get("files", {}),
                                                                    doc.get("game", []), doc.get("game_squads", {})))
                if path == "/api/layout/save":
                    with objects_lock:
                        done = scene.save_layout(doc["map"], doc.get("game", []), doc.get("mod", {}), doc.get("own"))
                    return self.send(200, done)
                return self.fail(404, "not found")
            except (ValueError, KeyError) as err:
                return self.fail(400, str(err))
            except Exception as err:
                return self.fail(500, f"{type(err).__name__}: {err}")

    return Handler


class _Server(ThreadingHTTPServer):
    # The page loads about twenty modules at once. With the default backlog
    # (5), Windows refuses the overflow, one module fails, and the editor
    # never starts (stuck on "Loading...").
    request_queue_size = 128
    daemon_threads = True


def serve(mod_dir, data_dir, port=8740, open_browser=True, game=None):
    """The editor for mod_dir; with none, the mod list first (picker.py), and
    the editor's Switch mod (POST /api/switch) goes back to it."""
    from ..modbuild import project_name
    from . import picker
    first = True
    while True:
        if mod_dir is None:
            if not game:
                raise SystemExit("no game folder found: give --game <folder with mods\\ and game_files\\>")
            mod_dir = picker.pick(game, port, _Server, open_browser and first)
            if mod_dir is None:
                return
            first = False                 # the picker's page goes on to the editor by itself
        scene = Scene(mod_dir, data_dir)
        server = _Server(("127.0.0.1", port), make_handler(scene, project_name(mod_dir)))
        server.switch = False
        server.can_switch = bool(game)
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"Foundry editor for {project_name(mod_dir)}: {url}  (Ctrl+C stops it)", flush=True)
        if open_browser and first:
            import webbrowser
            webbrowser.open(url)
        first = False
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return
        finally:
            server.server_close()
        if not server.switch:
            return
        mod_dir = None
