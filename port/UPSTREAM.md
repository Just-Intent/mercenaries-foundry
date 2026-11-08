# Offering the port patches upstream

Mercenaries Foundry's mods and plugins need the seven patches in `patches/`. Until
they are in [Mercenaries Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled),
players need a build with them applied. This is the plan for offering them as
pull requests. Nothing has been sent; opening a pull request is a public step for
the repository owner to decide.

All seven apply cleanly to upstream `master` (`f513ae4`, "Added DLL Plugin
System", the commit they were written on). Upstream's CONTRIBUTING.md asks for
focused, reviewable changes that say what was tested, so they are grouped by
what a reviewer has to check:

| Pull request | Patches | Size | Notes |
|---|---|---|---|
| 1. Plugin API additions | 0001 guest calls, 0003 script commands, 0004 tool windows, 0005 overlays, 0006 text input | ~420 lines in `sdk/mercenaries_plugin.h`, `plugin_host.*`, `main.c`, `dev_overlay.c` | Extends the existing plugin interface. No generated code changes; plugins that don't use the new entries are unaffected. Each was tested in game with a test plugin (`plugins/foundry-*-test`, Foundry Console). |
| 2. Pre-launch hook | 0007 launcher sync hook | 35 lines in `mod_loader.cpp` | Runs a command file beside the launcher before a modded launch, then rereads the mod list. Upstream may prefer a neutral name (e.g. `mod-prelaunch.cmd`) over `foundry-sync.cmd`; `mod sync --install-hook` can write whichever is chosen. Tested in game. |
| 3. Function hooks | 0002 function hooks | ~150 lines plus generated code | Needs rework first: `mercfoundry port-hooks` edits `src/recomp/gen` directly, and upstream treats that as disposable output. The hook entry points should be added by `ports/mercenaries/scripts/Patch-Generated.py` from a list of addresses (the list can come from `map/functions.json`). |

Checked against upstream `master`: pull request 2 applies on its own. Pull
request 1 doesn't yet, because 0003-0006 were written on top of 0002 and share
lines with it in `plugin_host.*`, `mercenaries_plugin.h` and `recomp_manual.c`.
Sending it first means rebasing 0003-0006 onto 0001 without 0002 (small
conflicts in those files), or sending 1 and 3 together once 0002 is reworked.

Every patch was written from the retail executable, the port's own code and live
tests, per [PROVENANCE.md](../docs/PROVENANCE.md). Upstream is MIT licensed, as is
this project.

## Steps when the owner decides to send them

1. Fork Mercenaries Recompiled on GitHub.
2. For each pull request, branch from upstream `master` and apply its patches:
   `git am port/patches/0001-*.patch port/patches/0003-*.patch ...`.
3. Push the branch to the fork and open the pull request with the notes above,
   the test evidence (docs/PLUGINS.md, docs/OVERLAY.md) and what remains
   unverified (only tested on Windows 11 with the US retail disc).
