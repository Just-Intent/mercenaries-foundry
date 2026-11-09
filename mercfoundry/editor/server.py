"""The editor's local web server (127.0.0.1 only; standard library).

GET  /                         the editor page (static/)
GET  /vendor/...               three.js (vendor/)
GET  /api/info                 {"mod": name, "maps": [...]}
GET  /api/terrain/<map>.json   terrain size, spacing and origin
GET  /api/terrain/<map>.bin    float32 heights, row by row
GET  /api/objects/<map>        the map's objects, with the mod's editor edits
GET  /api/model/<name>.glb     a model with its textures
GET  /api/bounds/<map>         each drawn model's bounds, for far-away stand-ins
GET  /api/layout/<map>         paths and zones: the game's (with the mod's layout
                               changes) and the mod's own (encounters/*.json)
POST /api/layout/save          {"map", "game": [...], "mod": {file: {...}}}
GET  /api/context/<map>        objects other enabled mods add, move or remove
POST /api/live/poll            the running game's plugin: its state in, commands out
GET  /api/live/state           connected?, the player's position, command results
POST /api/live/send            {"commands": [...]} for the game (see live.py)
GET  /api/encounters/<map>     encounters: the game's (read only) and the mod's
GET  /api/templates            objects a squad member can copy (template_*)
POST /api/encounters/save      {"map", "files": {file: [encounter, ...]}}
POST /api/save                 {"moved": {...}, "copies": [...], "removed": [...]}
                               -> objects/editor.json
"""
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import unquote, urlparse

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
    lock = threading.Lock()
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
                    with lock:
                        return self.send(200, {"mod": mod_name, "maps": scene.maps()})
                if path.startswith("/api/terrain/"):
                    name = path[len("/api/terrain/"):]
                    with lock:
                        meta, heights = scene.terrain(name.rsplit(".", 1)[0])
                    if name.endswith(".json"):
                        return self.send(200, meta)
                    return self.send(200, heights, "application/octet-stream")
                if path.startswith("/api/objects/"):
                    with lock:
                        objs = scene.objects(path[len("/api/objects/"):])
                        edits = scene.load_edits()
                    return self.send(200, {"objects": objs, "edits": edits})
                if path.startswith("/api/bounds/"):
                    with lock:
                        return self.send(200, scene.bounds(path[len("/api/bounds/"):]))
                if path == "/api/live/state":
                    return self.send(200, live.state())
                if path.startswith("/api/context/"):
                    with lock:
                        return self.send(200, scene.context_objects(path[len("/api/context/"):]))
                if path.startswith("/api/encounters/"):
                    with lock:
                        return self.send(200, scene.encounters(path[len("/api/encounters/"):]))
                if path == "/api/templates":
                    with lock:
                        return self.send(200, scene.templates())
                if path.startswith("/api/layout/"):
                    with lock:
                        return self.send(200, scene.layout(path[len("/api/layout/"):]))
                if path.startswith("/api/model/") and path.endswith(".glb"):
                    with lock:
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
                if path == "/api/live/send":
                    return self.send(200, {"queued": live.send(doc.get("commands", []))})
                if path == "/api/save":
                    with lock:
                        n = scene.save_edits(doc.get("moved", {}), doc.get("copies", []), doc.get("removed", []))
                    return self.send(200, {"rules": n, "file": f"objects/{scene.rules_path().name}"})
                if path == "/api/encounters/save":
                    with lock:
                        return self.send(200, scene.save_encounters(doc["map"], doc.get("files", {})))
                if path == "/api/layout/save":
                    with lock:
                        done = scene.save_layout(doc["map"], doc.get("game", []), doc.get("mod", {}))
                    return self.send(200, done)
                return self.fail(404, "not found")
            except (ValueError, KeyError) as err:
                return self.fail(400, str(err))
            except Exception as err:
                return self.fail(500, f"{type(err).__name__}: {err}")

    return Handler


def serve(mod_dir, data_dir, port=8740, open_browser=True):
    from ..modbuild import project_name
    scene = Scene(mod_dir, data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(scene, project_name(mod_dir)))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Foundry editor for {project_name(mod_dir)}: {url}  (Ctrl+C stops it)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
