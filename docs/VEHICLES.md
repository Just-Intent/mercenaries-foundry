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

## Changing handling live

Traced 2026-11-17 for plugins: a vehicle actor keeps its physics object inside itself, and the class is told by the actor's vtable (the pointer at +0 of the actor):

| Actor vtable | Kind | Physics in the actor | Set a value |
|---|---|---|---|
| `0x2E16D0` | wheeled (jeeps, trucks, cars) | `+0x2C0` (`WheeledActor_GetPhysics`, slot `+0x290`); its drivetrain at `+0x360` | `Drivetrain_SetPhysicsValue(actor + 0x360, hash, value)` for the engine, gears, wheels, suspension, brakes, drag and steering angle (31 keys) |
| `0x2E1F08` | helicopter | `+0x2C8` | `Helicopter_SetPhysicsValue(actor + 0x2C8, hash, value)` (14 keys) |
| `0x2E3760` | tank | `+0x2E4` | `TrackedVehicle_SetPhysicsValue(actor + 0x2E4, hash, value)` (7 keys) |

`hash` is the property's name hash (`HashName`, e.g. `physicsenginetorque`). Each class's vehicle setup is slot `+0x1C` of its vtable (`0x465F0`, `0x4A1D0`, `0x5F450`), which reads the template's `physics*` properties through these setters (`Drivetrain_ReadProperties`, `Helicopter_ReadProperties`, `TrackedVehicle_ReadProperties`). Wheel friction is copied to the tyres once (`WheeledVehicle_ApplyWheelFriction`, `0x133640`), so a plugin changing it calls that again. Not yet tried live: whether every value takes effect without the rest of the setup running again.
