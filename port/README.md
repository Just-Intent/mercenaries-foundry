# Changes to Mercenaries Recompiled

Mercenaries Foundry needs a few additions to the port's runtime. They live here as patches against the port (MIT; see its license) until they're upstreamed.

| Patch | What it adds |
|---|---|
| `0001-Plugin-guest-calls-...patch` | Plugin guest calls: `call_guest`, `guest_scratch` and `in_gameplay` on `MercPluginHost`. See [docs/PLUGINS.md](../docs/PLUGINS.md). Made against port commit `f513ae4` (v1.1.1). |

Apply and rebuild (Git Bash, from this repository's root, with the port checked out and built once in `external/Mercenaries-Recompiled`):

```bash
git -C external/Mercenaries-Recompiled am ../../port/patches/*.patch
```

```bash
external/Mercenaries-Recompiled/.venv/Scripts/cmake.exe --build external/Mercenaries-Recompiled/build/mercenaries --config Release --target mercenaries_recomp -- -m -v:minimal
```

Only the runtime (`mercenaries_recomp.exe`) changes; the launcher and generated code don't. Close the game before rebuilding.
