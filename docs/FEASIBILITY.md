# Feasibility notes: sky and weather, new maps, factions, objects, live editing

Research done 2026-11-15 from the retail data, the retail scripts and the
port's recompiled code; nothing here is tested in game yet.

## Sky and weather: feasible per map, and switchable in missions

Built in 0.102: `sky/<map>.json`, the editor's Sky panel, weather zones, and missions' Weather events. Not tested in game yet.

- The settings are in the loose per-map config files, not in the archives.
  The base block is in the map's `ConfigFile` (`DATAxbox/SW/SW.ini`, named
  by `levels.ini`): `SunAngle`, `Dark/NormalSkyRGB`, `Dark/NormalSunRGB`,
  `FogRGBA`, `SkyTexture skySummer`, `Rain`, `FogFunction`,
  `Atmospherefade`, the water lighting keys, plus `[SoundAmbience]`.
- Named presets are `[Atmosphere]` sections, mostly in the `*_XBOX.ini`
  files: 25 in `SW/SW_XBOX.ini`, 27 in `NW/NW_xbox.ini`, 9 in
  `SWN_XBOX.ini`. Their keys:
  - `Name`, `Fadetime`;
  - `SunlightRGB`, `AmbientRGB`, `HorizonRGB`, `SkyRGB`, `ZenithRGB`,
    `SunRGB`, the sun's flare, ray and disc sizes;
  - `SnowChance`, `RainChance`, `LeafParticlesPerSecond`, `FogRGBA`;
  - `SkyTexture` (`sky_heaven`, `sky_darkovercast`),
    `SkyRotationsPerMinute`;
  - `Glow*`, five `ProcessRGBA` colour grades, `TerrClouds*` (cloud shadows
    on the ground), `FlatSky*`.
- A preset comes on inside a `lineregion` boundary with property
  `type=atmosphere` and the preset's name. There are 98 such boundaries in
  the retail data. The toolkit can already add boundaries with properties.
- From Lua, `Mission_EnableAtmosphere(name, bool)` forces a preset. Retail
  uses `'prison'`, `'LaunchMt3'`, `'c17'` and `faction .. '_interior'`.
  Other calls: `Renderer_SetAmbientLight(r, g, b)`,
  `Effect_SetCloudFlightEffect`, `Renderer_SetMotionBlur`.
- There's no time-of-day cycle, and no Lua setter for fog, sky or rain;
  missions toggle presets.
- Open questions:
  - whether both `SW.ini` and `SW_XBOX.ini` load, and which wins;
  - whether a preset a mod adds can be forced from Lua;
  - what `Dark` and `Normal` mean;
  - what the 4th `FogRGBA` value does.

## New maps: feasible, not proven

Built as a trial in 0.106 (`maps/<name>.json`): the build copies a map's records under a new name, adds its level and config, and the terrain tools and editor work on it. Not tested in game: whether `Utility_ResetToBaseLayerOnly('<name>')` enters it is the open question. First try (0.106) froze at the loading screen: the copied load list loads a script named after the map (blank's master script is the script `blank`; a config's `MainMasterScript` names it otherwise), and it wasn't there. 0.111 adds it.

- A map is a set of `assets.dsk` records sharing its name: `Terrain`,
  `world`, `encounter`, `region`, `lineregion`, `path` and `list`. The
  `list` record is its load list.
- Loose files go with it:
  - a `[LV_x]` section in `DATAxbox/levels.ini` (Name, Path, AssetFile,
    TextureFile, WorldFile, ConfigFile);
  - a ConfigFile (radar texture, `MainMasterScript`, sky, water, traffic,
    ambience);
  - radar textures `rdr<map>` plus tiles in `streamed.dsk`.
- Terrain size comes from the Terrain record. The retail maps are 512×512
  at 8 m; `shl` is 128.
- `blank` is a starting point: one flat ground layer, a tiny world, and a
  script that prints "Hello World!".
- Map names hard-coded in scripts:
  - `global.lua` switches on 'sw', 'nw', 'swn' and others;
  - `bouncer.lua` has `validMaps = {'swn','sw','nw'}`;
  - the port's C code checks for sw and nw only.

  So the campaign plumbing (HQs, contracts) won't follow a new map without
  script work.
- First step: clone `blank` under a new name (all its records, a
  `levels.ini` section and its ini), reshape it with the terrain tools, and
  enter it with `Utility_ResetToBaseLayerOnly('<name>')` from Foundry
  Console.
- Open questions:
  - whether `levels.ini` is needed at all;
  - how terrain streaming is keyed;
  - whether the radar tiles are required;
  - whether larger terrain works.

## New factions: a truly new one isn't feasible; re-skins are

Re-skins built in 0.107 (`factions.json`: a faction's names in the game's text). Not tested in game.

- The executable's `Faction_FromHash` (0x95C20) is a fixed switch over 8
  slots: none, prokat/exops (the player), nk, sk, mafia, china, allies, civ.
  `FactionManager_SetRelation` (0x95DD0) rejects index 8 and above. Every
  slot is taken, and an unknown name maps to none.
- The scripts also hard-code the four employers. `bouncer.lua` builds
  templates, locations, strings and HQ blips from their names, and
  `gameflow.lua` sets 48 relation pairs.
- Feasible instead:
  - re-skin an existing faction: new display name, mood text, HQ and
    bouncer names, through text, world and script edits;
  - make a mission-only "faction": units that are really `nk` or `china`,
    with names, text and models swapped.

## New objects and buildings: feasible, with build work

New objects built in 0.103 (`models/<name>.new.json`, the editor's New object from a model); not tested in game yet. Buildings' ruin models and debris are still to do.

- Models are replace-only today: `_apply_models` needs the existing
  `Model` and `segments` records. A model copied under a new name would
  also need its own `NAME` chunk renamed.
- A new template already works in data. Copy an object as
  `template_<new>` with changed properties (an `objects/*.json` rule with
  `copy` and `as`, or a layer object `from`). Open question: whether the
  game draws a model from the object's `GeometryFile` property or from its
  object chunk's own model field.
- Buildings break by swapping models:
  - `geometrynameruined` (a ruin model; 5,299 retail objects have one);
  - `geometrynameruinA..E` with `nodenameruinA..E` (debris at joints);
  - `damageTexture`, `destructible`, `hitPoints`,
    `ObstacleMaterialType`;
  - collision flags 7 for the building, 15 for its ruin.
- Path:
  1. import a `.glb` onto a chosen base object;
  2. the build copies the base's model records under the new name and
     imports the glTF into them (likewise a ruin, or it reuses the base's);
  3. it adds the model to the layer's `[model]` load list;
  4. it writes a `template_<new>` object with `GeometryFile` and
     `geometrynameruined` changed;
  5. the editor places it like any template.

## Live editing: limited without a new native build

- Live ops today: move, remove, spawn (loaded templates), copy, respawn,
  inspect, loaded, teleport. There's no op to run Lua from the editor;
  adding one is a small change to the Foundry Console plugin, blocked on the
  development PC by Smart App Control.
- Workaround without a native build: `Foundry_Range()` reads
  `mods/Foundry Test Range/range.lua` (up to 3.7 KB) fresh on every call.
  The editor could write commands there, and a free-roam script could poll
  it on a timer. This is untested.
- Through that channel, things that could go live:
  - squad orders, music and atmosphere presets
    (`Mission_EnableAtmosphere`);
  - turning boundaries on and off;
  - loading layers that are already built.
- Rebuild only: terrain shape and paint, boundary and path shapes, new
  models and templates, text, object properties (the plugin has no property
  setter).
