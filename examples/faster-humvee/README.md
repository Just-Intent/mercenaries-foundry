# Faster Humvee

Edit the files in this folder, then build:

    python -m mercfoundry mod build . --data path/to/DATAxbox --out path/to/mods/Faster Humvee

- text/*.json: {"interface.shell.newgame": "NEW GAME"}; new keys are added.
- scripts/<name>.lua: replaces or adds the script <name>.
- objects/*.json: {"template": "player_humvee", "set": {"physicsenginetorque": "3400"}}
- textures/<name>.png: replaces or adds the texture <name> (power-of-two sizes).
- models/<name>.obj: an edited export from `python -m mercfoundry model`.

Get the originals to edit with `extract --decode`, `texture` and `model`.
