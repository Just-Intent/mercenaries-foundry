# Vehicle handling

Each vehicle's handling is a set of `physics…` properties on its world objects. Every copy carries its own values: the templates (`template_*`, used by `Spawner_SpawnTemplate`) and every vehicle already placed in a world layer (the Humvee at the first level's start is `player_humvee` in `swn_hq`). A mod object rule that matches on `model` reaches all of them.

```bash
python -m mercfoundry vehicles game_files/DATAxbox
```

```bash
python -m mercfoundry vehicles game_files/DATAxbox allies_veh_humvee -o my-mod/objects/humvee.json
```

The first lists the 92 vehicle models. The second writes a rule holding all of one model's handling values. Edit the ones you want, and delete the rest so the build leaves them alone. If copies of the model differ, the command warns that the rule will set them all to the template's values.

Tested in game (2026-11-08): tripling the Humvee's `physicsenginetorque` (850 to 2550) on all 66 Humvees made it accelerate much harder.

## What the game reads

Every value below is looked up by its name hash in `default.xbe`. The groups follow the code that reads them. The meanings come from the names and the retail ranges and haven't been measured; units are probably SI (kg, N·m, m/s, radians).

| Group | Read by | Values (retail range) |
|---|---|---|
| Wheeled: engine and gearbox | `0x136650`, `0x136AD0` | `physicsenginetorque` (50–2800), `physicsminrpm` / `optrpm` / `maxrpm`, `physicstorquefactoratminrpm` / `atmaxrpm`, `physicsupshiftrpm` / `downshiftrpm`, `physicsprimarytransmissionratio`, `physicsfirst…fourthgearratio`, `physicsreversegearratio`, `physicsfronttorqueratio` / `backtorqueratio` (drive split), `physicsdragarea` |
| Wheeled: wheels and body | `0x136650`, `0x136AD0` | `physicsmass`, `physicswheelmass`, `physicswheelwidth`, `physicssuspensionlength` / `strength` / `damping`, `physicsbraketorque`, `physicsmaxsteeringangle` (radians, 0.28–0.72), `physicsmaxspeedforfullsteering`, `physicsCollisionSpinDamping` |
| Wheeled: grip and driving aids | `0x1330F0`, `0x133AD0`, `0x133640`, `0x1346B0` | `physicswheelfrictionfront` / `back` (1.5–4.1), `physicsWheelFrictionFrontMax`, `physicsSteeringRate`, `physicsSlideBoost`, `physicsHillBoost`, `physicsSpoilerEffect` |
| Tanks | `0x141690`, `0x141750`, `0x138FC0` | `physicsForwardSpeed` (15–21), `physicsForwardAccel`, `physicsBackwardSpeed`, `physicsYawSpeed` / `YawAccel` / `YawCorrection` / `YawDamping`, `physicsbraketorque` |
| Helicopters | `0x138FC0` | `physicsclimbspeed` / `climbaccel`, `physicsSideSpeed` / `SideAccel`, `physicsMaxPitch` / `MaxRoll`, `physicsPitchAccel` / `RollAccel` |
| Airplanes | `0x147ED0` | `physicsMaxVelocity` (50–165), `physicsAccelerationRate`, `physicsMaxAngularVelocityYaw` / `Pitch`, `physicsMaxRoll`, `physicsRollDragModifier`, `physicsStandardAltitude` |
| Any physics object | `0x1319F0`, `0x393F0` | `physicsmass`, `physicsFriction`, `physicsRestitution`, `physicsBumplitude` |

Ground surfaces (`terrainPhysicsType`, [formats/dsk.md](formats/dsk.md)) are a separate system. Whether they change grip is still unknown.
