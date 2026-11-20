# Faction HQs: retail, and a new map's interiors

How the game's own HQ visits work, from its scripts (bouncer.lua,
briefing.lua, briefing_utilities.lua, briefing_config.lua,
briefing_interactions.lua, global.lua) and the recompiled code; then what a
mod's new map does instead (maps json `"interiors"`, missions.py
interiors_lua), and where the two still differ.

## Retail

### The way in: the bouncer (bouncer.lua)

- Each faction's HQ guard is `template_<faction>_bouncer`, spawned at
  `loc_hq-<faction>-bouncer` when free roam starts, if the faction has a
  contract left in the chapter's arc (and the map is one of its maps).
- It carries the faction's HQ objective (`TARGET_TYPE_HQ_<FACTION>`, blip
  `hq<faction>`), an `Event_ActorHitPointsLessThan` (killed: it respawns once
  the player is 300 m away) and an `Event_FactionAttitudeChange` (the use
  prompt switches between enter and bribe).
- Within 15 m it plays a mood animation every 5-10 s: `fb_mood_friendly`,
  `_neutral` or `_unfriendly` (none when hostile).
- Hostile: using it offers a bribe (`util_GetBribeCost(chapter)`; paid, the
  faction's mood goes to `util_GetPostBribeMood()`).
- Using it: the player and bouncer turn invincible, control is taken away,
  `current_faction` is written, the music stops and a bouncer banter line plays
  (`aso.bouaso01-05`, `sks.bousks0N`, `cso.boucso0N`, `mso.boumso0N`; the ace
  bouncer has chapter lines). When it ends: fade out (0.5 s), then
  `Briefing_Entry()`, every bouncer removed, the map's `mainkillbox` turned
  off, `Utility_OpenBriefingDataSets()`, and the briefing's layers read:
  `template_hq-<faction>` (the room), `<faction>_briefing`,
  `ch<chapter>_<faction>_briefing` and `<map>_<faction><n>_briefing`. The
  faction's briefing sound bank (`brief_un`, `brief_sk`, `brief_ch`,
  `brief_ru`), then `Briefing_Init('ignored', 'briefing')`.

### Inside: the briefing (briefing.lua, briefing_utilities.lua)

The HQ is its own scene: the room (`template_hq-<faction>-interior`) with
the boss, the second in command ("starter"), the starter trigger, the exit,
a chair and the faction's props. Its script:

- **Set-up** (`ScriptInit`): the room's atmosphere
  (`Mission_EnableAtmosphere('<faction>_interior')`), ambient light 80, 80,
  80, the HUD off (`Ui_EnableHud(FALSE)`), the music stopped, the mission's
  dateline (`Ui_DisplayDateline('mail.<asset>.dateline')`).
- **The player sits.** With no "aside" scene the player is seated in the
  chair at once (`AutoInteractWithChair`). The chair is a three-stage
  interaction: a sit-down animation, then a seated loop with use-only control
  (`Player_EnableUseOnlyControl`) and a "Stand up" prompt, then a stand-up
  animation. The player is attached to the room (`hp_player_chair`) and the
  **camera plays the room's own animation** (`Camera_ScriptPlayAnimation`,
  e.g. `allieshq_sitting_bosschair_camera`) in `CINEMATIC` mode. The follow
  camera only returns (`Camera_SetMode('MARIO64', blend)`) when the player
  stands up, with control mode 13.
- **What plays**: an aside (if the briefing has one), the greeting (from
  `ch<chapter>_<faction>_greetings`: friendly, neutral or unfriendly by the
  faction's mood toward the player; the allies always friendly), then the
  briefing body: voice lines and the boss's and starter's animations, played
  as a cinematic sequence (`Cinematic_BeginSequence`), the boss under
  cinematic control. Afterwards both look at the player. If the player does
  nothing, the boss nags: 3 times, after 4 s then 8 s.
- **Props**: one per contract has a voiced interaction (vending machine,
  coffee maker, dartboard...: briefing_config.lua); using a prop seats the
  player in its animation with its own camera.
- **The starter**: `starter_trigger` is usable (`actions.briefingstarter`,
  1.5 m) with a ring (`TARGET_TYPE_UNDEFINED`). Using it: `mission_accepted`
  = 1, `PlayerStartLocationName` = `loc_hq-<faction>-respawn` (or
  `loc_playerstart` for the playable intro and ace missions), the starter's
  closing lines, then out.
- **The exit**: usable after 5 s (`actions.briefingexit`, 0.5 m, a ring).
  Using it erases `current_faction` and sets `PlayerStartLocationName` =
  `loc_hq-<faction>-respawn`.
- **Out**: fade out, the atmosphere off, music and voice stopped,
  `Briefing_Deinit()`, the briefing sound bank unloaded. The world loads again
  and global.lua puts the player at `PlayerStartLocationName` (the
  faction's respawn point by the HQ); an accepted contract starts.

### In the engine

- `Briefing_Init` (0x175B10) sets the player up: control mode 13 (move,
  look, use: `Player_EnableControl` is a mode switch at 0xA3F80), the weapon
  put away (0x5CA70), the run permission cleared (+0x817), vtable +0x290.
  `Briefing_Deinit` (0x1758D0) undoes them.
- The follow camera (`MARIO64` / `RS_ACTOR_VIEW`, state at 0x9D840) looks
  from about 4 m behind and 0.8-1.9 m above the player, a distance fixed per
  actor kind. It casts a ray from its focus and tests every body whose
  collision shares a bit with 0x107, but it never comes closer than 0.75 m
  to its focus, and a ray starting inside a box ignores that box. Script
  commands can't change its distance (`Camera_SetZoom` and `SetOrbit` drive
  only the scripted camera).
- The scripted camera: `Camera_DetachFromActor`, `Camera_SetMode('RS_SCRIPT_VIEW')`,
  `Camera_SetPosition(x, y, z)`, `Camera_SetInterestActor(actor)` or
  `Camera_SetInterestPosition`; back with `Camera_SetMode('RS_ACTOR_VIEW')`
  (retail missions: aheart, allies1).

## A new map's interiors

A new map has no briefing scene of its own: its HQ room is a place on the
map, and using the guard fades out and moves the player there (missions.py
interiors_lua, from maps json `"interiors"`).

| Retail | A new map's interior | |
|---|---|---|
| Bouncer: enter, or bribe when hostile | The guard: same prompts, retail's bribe cost and mood | same |
| Bouncer banter line before entering | `"banter"`: true (the faction's retail lines) or cues; then in (0.232) | same |
| Bouncer mood animations within 15 m | `"guard_moods"` (on for bouncer templates): retail's fb_mood_* by attitude, about every 5 s (0.232) | same |
| Player and bouncer invincible while entering | leaders invincible | partly |
| Separate briefing scene, world reloaded after | a room on the map; fade and move, fade and move back | by design |
| `mainkillbox` off while inside | not needed (the room is in the map) | - |
| Control mode 13, weapon away, walking | the same (Foundry_HqMode) | same |
| Seated in a chair, camera on the room's authored shot | **0.231: a fixed camera in the room** (`"camera"`), on the boss while lines play, on the player otherwise; the follow camera again on leaving. No chair or seating | closer |
| Follow camera once standing (rooms built for it) | fixed camera throughout | differs |
| HUD off | on; `"hide_hud"` hides it (for voiced lines only: ours are on-screen messages) (0.232) | by choice |
| Room atmosphere, ambient light 80 | the map's own light | gap |
| Music stopped, room ambience cue | music stopped; `"ambience"` cue (Briefing_SetAmbientSoundCue), off on leaving (0.232) | same |
| Dateline shown | the offered contract's (`mail.<id>.dateline`, made from its title), once a visit (0.232) | same |
| Greeting by faction mood (friendly, neutral, unfriendly) | `"greeting"` on the first visit, then `"greetings"` by mood every visit (0.232) | same |
| Briefing as a cinematic sequence (VO, boss and starter animations) | subtitled lines with optional voice, leaders under cinematic control | partly |
| Boss nags (3 times) | `"nags"`: one line each, after 4 s then every 8 s, until accepted or left (0.232) | same |
| Voiced prop interactions | none | gap |
| Starter: ring, 1.5 m, `mission_accepted`, `PlayerStartLocationName` | ring and accept prompt; `loc_hq-<faction>-respawn` when the map has that faction's `"hq_respawn"` (0.232) | same |
| Exit after 5 s, ring, 0.5 m | after the exit's delay (5 s), ring, 3 m | same |
| Out: back at `loc_hq-<faction>-respawn` | back where the player stood by the guard | differs |

### The camera (0.231)

The complaint was a wall between the camera and the player inside. Retail
never shows the follow camera while you're being briefed: you sit, and the
camera plays a shot authored with the room. A room on the map gets the
follow camera, which is pushed past the walls in tight spots (above). The
interior now takes a camera spot (`"camera": {"position": [x, y, z]}`; the
editor's HQs panel sets it, 1.8 m above the view's centre) and uses
retail's scripted camera from there for the whole visit: on the boss while
lines play, on the player otherwise; leaving or taking the contract gives
the follow camera back. Without a spot it stands 1.2 m behind and 1.8 m
above the room spot. Put it high in a corner, clear of the walls.

### Still different

- Seating: retail's chair (sit, a seated loop, stand up) uses animations and
  hardpoints that only load with its briefing scene; the fixed camera does
  the chair's job of framing the briefing.
- Props with voiced interactions.
- The way out puts the player back by the guard, not at the respawn point
  (the world doesn't reload).

All of these are set in the editor's HQs panel (an interior's Lines,
Atmosphere and The guard sections).
