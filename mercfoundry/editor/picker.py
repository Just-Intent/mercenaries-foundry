"""Starting the editor without knowing the folders: the game folder and its
DATAxbox are found, and with no mod named a page lists the game's mods (open
one, or make a new one). The editor's "Switch mod" comes back here.

    mercfoundry edit                       the game folder this toolkit is in (or the current one)
    mercfoundry edit --game "D:\\Mercs"     another game folder
    mercfoundry edit "mods\\My Mod"          straight to a mod (the game found from the mod)
"""
import html
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse


def _is_game(folder):
    return (folder / "mods").is_dir() and (folder / "game_files").is_dir()


def find_game(start=None):
    """The game folder (it has mods/ and game_files/): the one given, else the
    one this toolkit is installed in (<game>/foundry/mercfoundry.pyz), else
    the current folder or one above it."""
    cands = []
    if start:
        cands.append(Path(start))
    here = Path(__file__).resolve()
    cands += list(here.parents)                    # inside <game>/foundry/mercfoundry.pyz
    cwd = Path.cwd()
    cands += [cwd, *cwd.parents]
    for c in cands:
        try:
            if _is_game(c):
                return c
        except OSError:
            continue
    return None


def find_data(game):
    """The game's DATAxbox (game_files/<copy>/DATAxbox), the retail copy first."""
    if not game:
        return None
    files = Path(game) / "game_files"
    found = sorted(files.glob("*/DATAxbox"), key=lambda p: (0 if "retail" in p.parent.name.lower() else 1, p.name))
    return found[0] if found else None


def mods(game):
    """The game's mod folders: [{folder, name, foundry (has mod.json), files}]."""
    out = []
    root = Path(game) / "mods"
    for d in sorted(root.iterdir() if root.is_dir() else [], key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        meta = d / "mod.json"
        name, desc = d.name, ""
        if meta.is_file():
            try:
                doc = json.loads(meta.read_text(encoding="utf-8"))
                name, desc = str(doc.get("name") or d.name), str(doc.get("description") or "")
            except (OSError, ValueError):
                pass
        out.append({"folder": d.name, "name": name, "description": desc, "foundry": meta.is_file(),
                    "maps": sorted(p.stem for p in (d / "maps").glob("*.json")) if (d / "maps").is_dir() else []})
    return out


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Foundry editor: choose a mod</title>
<style>
body {{ font: 14px system-ui, sans-serif; background: #1d2026; color: #e6e6e6; margin: 0; }}
main {{ max-width: 760px; margin: 40px auto; padding: 0 20px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} p.sub {{ color: #9aa3ad; margin: 0 0 24px; }}
.mod {{ display: flex; align-items: center; gap: 12px; background: #262a31; border: 1px solid #343a43;
        border-radius: 8px; padding: 12px 14px; margin-bottom: 8px; }}
.mod .t {{ flex: 1; }} .mod .n {{ font-weight: 600; }} .mod .d {{ color: #9aa3ad; font-size: 12px; margin-top: 2px; }}
button {{ background: #3d6fd6; color: white; border: 0; border-radius: 6px; padding: 7px 14px; font: inherit; cursor: pointer; }}
button.ghost {{ background: #343a43; }} button:disabled {{ opacity: .5; cursor: default; }}
form {{ display: flex; gap: 8px; margin-top: 24px; }} input {{ flex: 1; background: #262a31; color: inherit;
        border: 1px solid #343a43; border-radius: 6px; padding: 7px 10px; font: inherit; }}
.note {{ color: #9aa3ad; font-size: 12px; margin-top: 18px; }} .err {{ color: #ff8a80; margin-top: 10px; }}
</style></head><body><main>
<h1>Foundry editor</h1>
<p class="sub">Game folder: {game}</p>
<div id="list">{rows}</div>
<form id="new"><input name="name" placeholder="New mod's name, e.g. My Island" required><button>Make it and open</button></form>
<div class="err" id="err"></div>
<p class="note">A new mod is a folder in mods\\ with mod.json and the usual subfolders; build it from the editor's Changes panel or the launcher.</p>
</main><script>
async function go(body) {{
  const r = await fetch('/api/open', {{ method: 'POST', body: JSON.stringify(body) }});
  const j = await r.json();
  if (j.error) {{ document.getElementById('err').textContent = j.error; return; }}
  document.body.innerHTML = '<main><h1>Opening ' + j.name + '...</h1><p class="sub">The editor starts in a moment.</p></main>';
  const wait = async () => {{ try {{ const i = await fetch('/api/info'); if (i.ok) {{ location.href = '/'; return; }} }} catch (e) {{}}
                              setTimeout(wait, 500); }};
  setTimeout(wait, 800);
}}
document.querySelectorAll('[data-open]').forEach(b => b.onclick = () => go({{ folder: b.dataset.open }}));
document.getElementById('new').onsubmit = e => {{ e.preventDefault(); go({{ new: e.target.name.value }}); }};
</script></body></html>"""


def page(game):
    rows, others = [], []
    for m in mods(game):
        if not m["foundry"]:              # plugins and built mods: not opened here
            others.append(html.escape(m["folder"]))
            continue
        what = ("maps: " + ", ".join(m["maps"])) if m["maps"] else (m["description"] or "Foundry mod")
        rows.append(f'<div class="mod"><div class="t"><div class="n">{html.escape(m["name"])}</div>'
                    f'<div class="d">{html.escape(m["folder"])} &middot; {html.escape(what[:120])}</div></div>'
                    f'<button data-open="{html.escape(m["folder"])}">Open</button></div>')
    body = "".join(rows) or "<p>No Foundry mods yet: make one below.</p>"
    if others:
        body += (f'<details class="note"><summary>{len(others)} other mods (plugins and built mods, not '
                 f'Foundry projects)</summary>{", ".join(others)}</details>')
    return PAGE.format(game=html.escape(str(game)), rows=body)


def pick(game, port, server_cls, open_browser):
    """Serve the mod list until one is chosen; returns its folder."""
    chosen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            body = (json.dumps(body) if isinstance(body, (dict, list)) else body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html"):
                return self.send(200, page(game), "text/html; charset=utf-8")
            return self.send(404, {"error": "choose a mod first"})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)
            if path != "/api/open":
                return self.send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if body.get("new"):
                    name = str(body["new"]).strip()
                    folder = re.sub(r'[<>:"/\\\\|?*]', "", name).strip(". ")
                    if not folder:
                        return self.send(400, {"error": "give the mod a name"})
                    target = Path(game) / "mods" / folder
                    if target.exists():
                        return self.send(400, {"error": f"mods/{folder} already exists: open it instead"})
                    from ..modbuild import new_project
                    new_project(target, name)
                else:
                    target = Path(game) / "mods" / str(body.get("folder") or "")
                    if not target.is_dir() or target.parent != Path(game) / "mods":
                        return self.send(400, {"error": "no such mod folder"})
                    if not (target / "mod.json").is_file():
                        return self.send(400, {"error": f"mods/{target.name} isn't a Foundry project (no mod.json)"})
                chosen["folder"] = target
                from ..modbuild import project_name
                self.send(200, {"name": project_name(target)})
                threading.Thread(target=server.shutdown, daemon=True).start()
            except Exception as e:  # noqa: BLE001 - shown on the page
                self.send(500, {"error": str(e)})

    server = server_cls(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Foundry editor: choose a mod at {url}  (Ctrl+C stops it)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
    finally:
        server.server_close()
    return chosen.get("folder")
