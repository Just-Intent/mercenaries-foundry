# The port fork, and upstream

## Where the port lives

- `external/Mercenaries-Recompiled` is a submodule on
  [Just-Intent/Mercenaries-Recompiled](https://github.com/Just-Intent/Mercenaries-Recompiled),
  GitHub's fork of [KraftMacAndChee/Mercenaries-Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled).
- Our changes are on its branch `foundry-plugin-calls`. The fork's `master`
  mirrors upstream.
- In the checkout, the remote `origin` is the fork and `upstream` is
  KraftMacAndChee's repository.
- The toolkit repository records which port commit it was built and tested
  with (the submodule's commit).

## Taking upstream's changes

In `external/Mercenaries-Recompiled`:

1. `git fetch upstream`
2. `git log --oneline foundry-plugin-calls..upstream/master` shows what is new.
3. `git checkout foundry-plugin-calls`
4. `git merge upstream/master`. Fix any conflicts. Ours are mostly in
   `plugin_host.*`, `mercenaries_plugin.h`, `recomp_manual.c` and
   `Patch-Generated.py`.
5. Rebuild the port (the port's build script, or MSBuild on
   `build/mercenaries/mercenaries_recomp.vcxproj`). Then run
   `python -m mercfoundry port-hooks`, rebuild again, and test in game.
6. `git push origin foundry-plugin-calls`. Optionally also update the fork's
   `master`: `git push origin upstream/master:master` (or use "Sync fork" on
   GitHub).
7. In the toolkit repository, `git add external/Mercenaries-Recompiled` and
   commit, to record the new port commit.

`src/recomp/gen` is generated output: if upstream regenerates it, rerun
`port-hooks` rather than merging hook wrappers by hand.

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

1. The fork exists (above).
2. For each pull request, branch from upstream `master` and apply its patches:
   `git am port/patches/0001-*.patch port/patches/0003-*.patch ...`.
3. Push the branch to the fork and open the pull request with the notes above,
   the test evidence (docs/PLUGINS.md, docs/OVERLAY.md) and what remains
   unverified (only tested on Windows 11 with the US retail disc).
