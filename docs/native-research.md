# Native research: first engine access established

The project now includes a compiled-and-exercised diagnostic DLL **source**, a
build script, a static analysis tool and a Frida-based live investigation harness.
It still does not implement a multiplayer game adapter.

## Observed on October 2, 2026

Executable: Windows x64 Steam build 19149099, SHA-256
`8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c`.

The final 12-second diagnostic run:

- Loaded our x64 DLL inside an actual Reassembly process.
- Sampled 12 populated zones through native simulation callbacks.
- Read command-ship presence, faction, position and velocity.
- Dispatched `Body::setVel` to the second observed root command ship.
- Read back velocity immediately: **before `[0,0]`, after `[30,0]`**.
- Subsequent zone records contained moving ships and changing cluster counts.
- No instrumentation error was reported in this successful run.

This was the **animated menu/demo world**, not a campaign. Vanilla AI continued
running and can override the injected velocity. It is evidence that native world
inspection and an engine setter are usable, not proof of a human controller,
network replication, simultaneous playable factions, or multiplayer persistence.

Local evidence for this run is in ignored
`.runtime/probe-user-1790985320645873100/`: `native-telemetry.jsonl`,
`instrumentation.json`, `summary.json`, and the isolated game's own log.
Addresses and thread IDs vary between runs; never reuse captured pointers.

## Layout evidence

These offsets are specific to the executable hash above. They were recovered
from current machine code, not copied from the 2019 struct declarations.

| Object / field | Offset or exported RVA | Evidence |
| --- | --- | --- |
| `GameZone` cluster vector | `+0x188` | Exported `getClusters` returns `this + 0x188` |
| `BlockCluster` faction | `+0x118` | Exported `getFaction` reads a 32-bit integer there |
| `BlockCluster` command | `+0x108` | `Block::getCommand` follows its cluster then reads this offset |
| `BlockCluster` parent body cluster | `+0x178` | Exported `getBody` selects that pointer when non-null |
| `Body` position | `+0x30`, `+0x38` | Exported `getPos` reads doubles then converts to floats |
| `Body` velocity | `+0x40`, `+0x48` | Exported `setVel` converts float inputs to doubles there |
| `Body` angle | `+0x60` | Exported `getAngle` reads a float there; setter readbacks verified |
| `AI` zone | `+0x228` | `estimateTargetPos` accesses that zone's simulation time |
| `AI` command | `+0x278` | `getFaction` and `AIAction::getCluster` dereference it |
| `GameZone::Update` | RVA `0x1641b0` | Named export; live callback successfully observed |
| `AI::update` | RVA `0x74150` | Named export; secondary callback successfully observed |
| `Body::setVel` | RVA `0x5cbe0` | Named export; native invocation and immediate readback verified |

`Body` is distinct from its embedded `cpBody`: `getBody` returns the latter at
an additional `+0x10`. The diagnostic reads `Body` fields relative to the former.
An early experiment confused these addresses and produced zero telemetry; the
corrected implementation was rerun and observed moving positions and velocities.

The Windows x64 ABI passes the 8-byte float2 setter argument as an aggregate in
RDX. The actual setter disassembly confirms that behavior. The diagnostic packs
two floats into a 64-bit value, resolves the current export by name, and calls
from the simulation callback thread. Short instruction signatures are also
checked before the build-specific access is enabled. The harness checks the full
executable hash before spawning or instrumenting.

## Reproduce

Research dependencies live within ignored `.runtime/`; no global Python
installation was changed:

```powershell
python -m pip install --target .runtime/python-tools capstone frida pefile
python tools/analyze_native.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe"
```

An official Zig 0.15.2 Windows x64 compiler was downloaded into
`.runtime/toolchain/` and its archive SHA-256 was checked against Zig's official
download index. The compiler is portable; it is not installed into system PATH.
With that compiler already unpacked:

```powershell
python tools/build_native.py
python tools/run_native_probe.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --seconds 12 --test-control
```

Alternatively pass `tools/build_native.py --zig <path-to-zig.exe>`.
The experiment launches its own game process. Shell user-folder queries and
user-profile environment values are redirected to a fresh project-local test
directory, and `SteamAPI_Init` is replaced with failure before the game starts.
The game log confirms Steam is disabled and data is written under that test
profile. Networking is disabled through `kNetworkEnable=0`. This is save-profile
isolation, **not** an OS security sandbox. No existing campaign is opened.

After the observation period the harness terminates only its own spawned PID.
It preserves test artifacts. `--test-control` makes one velocity change to a
second root command ship; omit it for read-only world sampling. A normal window
can briefly appear during this diagnostic run.

## Headless findings

Passing `kHeadlessMode=1` alone caused the game to report that input files were
required and exit. Supplying `kInputPath` loaded and inspected a ship, but no
simulation callback was observed during the bounded run. A directory input plus
tournament flags also did not establish a headless simulation. These attempts are
recorded as failures rather than counted as successful native tests.

Current-binary analysis located the sandbox dispatch: a nonempty
`kSandboxScript` invokes a separate headless world loop. Commands must be
semicolon-separated; `cursor` requires all three values (x, y, angle).
`sleep 10` must have a subsequent command to keep the queue active while time
advances. A script importing two stock ships, activating the second, sleeping,
then echoing a completion marker ran **601 state and 601 zone iterations**.
Native telemetry captured 31 populated samples on the simulation thread,
verified the second ship's velocity setter, and observed its position advancing.
The first ship remained stationary in this fixture. Both ships used faction 8;
this does not demonstrate separate player factions.

Evidence is in `.runtime/probe-user-1790985725951042200/`. Reproduce without
copying or redistributing game assets:

```powershell
python tools/run_native_probe.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --seconds 12 --two-ship-sandbox --test-control
```

The harness verifies that two ships exist and at least one position advances.
The headless simulation runs faster than wall time. Dedicated **campaign**
hosting, pacing, input dispatch and save restoration remain unverified.
The harness also supports custom `--sandbox-file` scripts for follow-up research.

## Remaining native milestones

### Persistent IDs and live cluster scenes

The latest fixture selects pose targets by `SerialCommand.ident`, not faction.
The current equality export accesses the identifier at SerialCommand +8;
`AI::getFaction` verifies the command pointer and faction path. A live cluster's
command is at +0x108, its SerialCommand pointer at command block +0x28.
Unidentified command ships receive host-assigned IDs beginning at 0x70000000,
skipping observed IDs. This allocation is a fixture policy, not a complete
campaign epoch/collision scheme. Noncommand debris uses the first block's
`persistentIdent` at block +0x30, matching current `clearPersistentIdent` code.

The ordinary `import` path can reset inactive ship IDs. Native `level_load`
preserves them; initial bindings now require an exact ID and faction match.
The pose fixture tests matching native IDs against the serialized bootstrap.
Evidence: `.runtime/replication-check-1790991046668843600/`.

```powershell
python tools/check_native_replication.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --live-clusters
```

This mode exports every root cluster using native serialization, transfers a
bounded SHA-256-verified scene over TCP, and reconstructs it repeatedly in a
second headless game. The client applies the game's clear/load functions on
the simulation thread. The inspected console helper provides the live console
context; each native call verifies that context still refers to the same zone.
The level handler takes raw command text in R9, not an MSVC string object.
The loader adds the current sector center to positions; the adapter reads that
center and removes the translation for these absolute-coordinate snapshots.

The host fires real weapons and injects one native `Block::removeHealth` call
to destroy a faction-8 thruster. The verified client cluster counts were
**2,4,5,5,6,7,5**, and thruster counts changed from **22 to 21**. Every applied
scene is reserialized from the client and checked against the source for entity
IDs, factions, poses and block type/offset/angle topology. Block order can change
during reconstruction; the comparison ignores vector order. Evidence:
`.runtime/replication-check-1790991362034620800/`.

`tools/scene_codec.py` reads this native text subset without executing Lua.
It bounds bytes, token count and nesting, rejects duplicate IDs, and validates
structure before a live snapshot reaches the game loader. Its geometry check
deliberately reports only geometry/identity equivalence; byte hashes track
complete payload differences separately. Matching geometry does not establish
equivalent health or transient attributes.

The stronger checks found that reconstructed launchers/missiles gain default
fields such as capacity, PERISHABLE flags and lifetime, and zero AI flags can
expand to faction defaults. Results therefore explicitly record
`byteIdenticalLiveSnapshotsValidated=false` and
`hydrationDifferencesDetected=true`. Those differences still prevent a claim
of complete combat fidelity. This mode also replaces a whole scene at roughly
one-second intervals, reconstructing objects instead of applying measured
entity/block deltas. It does not synchronize the separate projectile vector,
resource pockets, input events, distant galaxy sectors or campaign progression.

### Network waypoint controls and two-process poses verified

The diagnostic now resolves the native `AI::clearCommands` and
`AI::appendCommandDest` exports. The latter passes a packed float2 in RDX and
its radius in XMM2. `Block::getCommandAI` resolves the controller from the
current command block. Commands run on the simulation callback thread, with
current clusters resolved each time. Short signatures and the full executable
hash guard this build-specific code.

`--network-control-test` uses a separate **research** TCP bridge on an ephemeral
loopback port. It authenticates each connection to an assigned empire, rejects
cross-empire commands and sequence replay, bounds message sizes and coordinates,
and queues waypoints for the native simulation thread. It is not attached as
the production transport's engine and does not advertise its full capabilities.

```powershell
python tools/run_native_probe.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --seconds 8 --network-control-test
python tools/run_native_probe.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --seconds 8 --network-control-test --same-content-factions
python tools/check_native_replication.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe"
```

The first network run drove faction 7 and faction 8 toward separate destinations.
The same-content run instantiated two stock faction-8 Interceptors with command
factions **10008** and **20008**; both moved, and neither network client could
command the other's empire. It verifies command affiliation, not all faction
data, progression, diplomacy, or campaign relocation behavior. Generated stock
asset copies remain under ignored `.runtime/` and are not redistributed.

The replication fixture launches **two real Reassembly processes**. The host
receives the TCP client commands; its sampled native poses travel over a local
TCP stream to the replica, which applies exported cluster position/angle and
body velocity setters on its simulation thread. Setter readbacks verify all
five pose values. Replica vanilla AI is suppressed in this fixture. The test
requires at least three successful updates and meaningful movement for both
factions. Positions use float setters, so comparisons allow 0.1-unit rounding.

Evidence includes `.runtime/probe-user-1790987760380078600/` for TCP controls,
`.runtime/probe-user-1790988036825662800/` for same-content empires, and
`.runtime/replication-check-1790988083011553000/` for poses including angles.
Each reproduction writes a new evidence directory.

These are fixed two-ship **headless** research fixtures. The later bootstrap
extension sends the host's actual serialized ships (including block payloads)
over TCP; the replica imports those files instead of independently loading stock
assets. It does not yet send subsequent block changes, spawn/despawn events,
damage, projectiles or resources. Faction IDs identify the
two fixture ships; production needs persistent entity IDs and multiple ships
per empire. Latest poses may replace queued older poses; this is not measured
production snapshot pacing or interpolation. Waypoints are vanilla navigation
commands, not exclusive player thrust/aim/fire input. No rendered interactive
client or playable campaign has been verified.

### Native firing and ship serialization verified

`--weapon-fire-test` suppresses vanilla AI updates and invokes the exported
`FiringData(Block*)` constructor followed by `AI::fireWeaponsAt`. The constructor's
field writes were inspected in this exact executable; the diagnostic uses an
aligned opaque buffer and sets the observed aim-position field at +8. Native
telemetry observes positive fire results and additional projectile/missile
entities. Firing can grow the cluster vector, so iteration stops immediately
after invoking it. This fixture targets the first matching command ship; full
entity selection and network aim/fire scheduling remain unimplemented.

`BlockCluster::toString` serializes an actual ship, including its block payload
and command identifier. Its MSVC string return layout and native cleanup call
were recovered from current machine code. The diagnostic invokes the game's
own cleanup, with a build/signature guard, rather than freeing engine memory
with the DLL allocator.

```powershell
python tools/run_native_probe.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe" --seconds 5 --weapon-fire-test
python tools/check_native_cluster.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe"
```

The cluster round trip exported a stock Interceptor from one native process,
imported that payload into a second process, and exported it again. The two
2,635-byte payloads were **byte-identical**. This tests that one serialized ship;
it does not prove preservation of every transient combat field or modded ship.
Evidence: `.runtime/cluster-check-1790988521988435200/` and
`.runtime/probe-user-1790988369362025800/` for firing.

The updated replication fixture transfers both host-produced serialized ship
payloads over TCP with SHA-256 verification, reconstructs them in the client
process, and then streams poses. This later result is in
`.runtime/replication-check-1790988609557680100/`. Bootstrap and pose checks
remain separate from the production transport's complete engine contract.

### Native sector save/restore verified

`tools/check_native_restore.py` runs two isolated game processes. The first
imports two ships in sector {0,0}, injects a nonzero velocity, and invokes
`level_save checkpoint.lua`. The game writes `checkpoint.lua.gz` under the
isolated user root. The second process receives a copy and loads its absolute
path through `level_load`, reconstructing the ships using the game's parser.

The check compares native telemetry against metadata in the checkpoint: ship
count, command presence, factions, positions (with the level offset applied),
and velocities within 0.1 units. It also requires motion after restoring.
The file includes the game's serialized block payload; this check does **not**
verify every block field or AI state. Process addresses are deliberately ignored.

```powershell
python tools/check_native_restore.py --exe "D:\SteamLibrary\steamapps\common\Reassembly\win64\ReassemblyRelease.exe"
```

The successful round trip's evidence is stored under
`.runtime/restore-check-1790986617854262300/` (consult the tool output for the
actual directory on subsequent runs). Unsupported filenames such as
`checkpoint` and `checkpoint.gz` were rejected before filesystem access;
the `.lua.gz` suffix succeeds. The checkpoint format mixes text and binary
inside gzip; it must not be decoded as ordinary UTF-8 Lua text.

An earlier save with ships on opposite sides of the sector boundary saved only
one ship. This is a **sector** serializer, not a complete campaign checkpoint.
Galaxy sectors, player progression, faction ownership, AI state and modded
content restoration still require separate investigation.

1. Establish fresh campaign startup beyond the verified sandbox and track the
   campaign's zone/player/save lifetimes rather than the menu demo.
2. Extend verified empire waypoint ownership and native aim/fire into exclusive
   human thrust/aim/fire controls and fleet commands, with release-on-disconnect.
3. Extend verified live cluster geometry, IDs and block destruction into full
   transient combat fidelity, projectiles/resources and entity/block deltas;
   establish campaign-safe identity allocation and distant-sector streaming.
4. Connect an adapter to the transport only when it really satisfies the
   advertised authoritative-world, multi-faction and checkpoint-restore contract.
5. Extend the verified two-headless-process fixture into rendered interactive
   game clients, followed by saving and resuming a shared campaign.

No developer contact was needed for the native results above. No third-party
game headers or proprietary binaries were added to the tracked project.
## Runtime health and serialized-state fidelity

The continued live fixture now exercises authenticated TCP `fire` inputs as
well as faction waypoints. The research bridge assigns the owner from its
connection token, rejects another faction's firing command, and rejects a
replayed sequence. The simulation thread dispatches an accepted firing command
through the existing inspected `AI::fireWeaponsAt` wrapper. This remains the
research protocol; it does not satisfy the production adapter contract.

`RepopulatedPartialDamageFixture` calls the inspected native health-removal
function with one quarter of a live thruster's health and verifies a positive,
reduced health readback. A subsequent snapshot includes the damaged thruster;
the client reconstructs it before the later destructive fixture removes it.

`RepopulatedReadHealth` independently reads each root entity's actual block
health at the machine-code-verified offset `Block + 0x4c`. Entity IDs, vector
bounds, finite values, and duplicate entity matches are checked. Both processes
sample health immediately around serialization/reconstruction on their
simulation threads. The harness compares sorted health vectors per entity,
with an absolute tolerance of 0.02 for native text precision. This verifies
health multisets, not persistent identity of every individual block.

Evidence: `.runtime/replication-check-1790992531870285300/result.json`.
Seven replica snapshots compare 66–69 blocks each, with health multisets
matching throughout. The nonlethal thruster reaches the replica at 53.85 health;
its later destruction also remains verified. Two authenticated native firing
commands are dispatched, and at least one reports weapon firing.

`tools/scene_fidelity.py` additionally compares all fields exposed by native
serialization, independently of block-vector order. Missing fields remain
differences; no engine defaults are guessed. Each snapshot report includes a
bounded list of paths, presence, and values, with a total count even when the
list is truncated. The latest native run still reports changed command AI
flags and missile capacity/features/lifetime. Serialized equivalence remains
false, and fields omitted by serialization remain outside this audit. These
results must not be promoted to full-world or playable multiplayer validation.

## Normal campaign host and rendered remote pilot

The next prototype is `tools/native_coop.py`, with its launch UI in
`tools/native_launcher.py` and [setup/limits](native-alpha.md). A new private
campaign fixture starts through `kLoadSlot=0` and reaches `GSFly`. Its generated
map uses stock region 200. A nonempty metamap is necessary: an empty objectives
vector causes a zero-width native spatial hash and a divide-by-zero during
campaign warp generation. The fixture objective is a startup record, not proof
of a real generated station. Stock SaveData faction 8 supplies the blueprint;
the host player has native faction 100 and the remote ship faction 20008.

Before Player::onContinue (RVA `0x1dd560`) searches the fresh zone, the harness
clones the loaded blueprint, sets each block's faction through native
SerialBlock::setFaction, adds the cluster through addToGameZone, and binds its
command identity. Global SaveData/player pointer storage at `0x3cf700` and
`0x3cf930` and the blueprint field at SaveData `+0x20` are verified for the exact
executable hash. This is fixture startup, not arbitrary existing-save import.

Remote movement uses native getNavConfig and sNav::update (`0x43ef0`) to drive
thrusters toward the requested velocity and angle. It does not set host ship
position or velocity directly. Only the remote command ship's AI is suppressed;
vanilla NPC AI, the host player's AI::playerUpdate, and missile AI remain active. Both
movement and firing select the owned ship identity, not any same-faction
missile. An interest exporter bounds snapshots to 2,000 units around that ship.

The client uses rendered GSEditor/GSGod mode. Its native zone update bookkeeping
continues, while cpSpaceStep (`0x2eca30`), Block::update, and AI::update are
suppressed so it cannot advance authoritative combat independently. Native
level_load reconstructs snapshots on the simulation thread. Its sector-relative
translation is retained for rendering; the camera follows the reconstructed
pilot through the verified View position/scale fields. Block rendering and SDL
frame presentation are counted independently from snapshot application.

SDL rewrites its dynamic API entry stubs during startup. Early hooks on
SDL_GL_SwapWindow and SDL_PollEvent were lost, producing misleading zero-frame
observations even while DrawGame continued. Installing SDL hooks on the first
DrawGame call establishes ongoing frame and actual keyboard-event observation.
Frida's bool NativeCallback return must be integer 0, not JavaScript false.

Evidence: `.runtime/coop-check-1790996362396019900.json`. A thirty-second
two-process TCP check applied 112 client snapshots and observed 6,983 presented
frames, 39,590 pilot block rendering calls, 264 successful native drives, 12
successful firing inputs, over 538 units of displacement, and one respawn after
deliberate native command-block destruction. The later non-scripted-input
ninety-second client run in `.runtime/coop-check-1790996574315609100.json`
applied 340 snapshots and presented 21,337 frames without reported failures.
Neither result proves physical keyboard handling or a two-computer LAN session.

The original headless regression checks still pass after these changes:
`.runtime/drive-check-1790997274425636300/result.json` and
`.runtime/replication-check-1790997274425636300/result.json`. Production adapter
capabilities remain blocked. Remote building, progression, mod loading, full
galaxy streaming, checkpoint/resume, bullet/beam effect replication, and
dedicated campaign simulation remain unresolved.

## Shared block-stat research

The separate build-menu-filter work reported additional exact-build findings;
its `addons/build-menu-filter/runtime.js` uses these addresses and fields. They
are recorded here for future adapter work, without changing the multiplayer
DLL or claiming independent multiplayer validation of those stats:

- Template lookup: RVA `0x25b2e0`, taking a block ID and returning SerialBlock.
- Native getters: mass `0x5c940`, maximum health `0x5c8e0`, area `0x5c8c0`,
  growth time `0x5c9c0`, cost/deadliness `0x231c80`, weapon DPS `0x232300`,
  weapon range `0x232400` (SerialBlock pointer, uint64 feature mask).
- SerialBlock resource capacity `+0x38`, health `+0x34`, shield pointer `+0x88`,
  BlockType pointer `+0x90`. These are SerialBlock-relative offsets; a runtime
  Block's serial data begins at `+0x18`.
- BlockType power capacity `+0x30`, thruster force `+0x34`, generator rate `+0x64`.

The counterpart reported disassembly of the native stats formatter at
`0x1ed200` and live palette checks. Independently check getter signatures,
units, and resource semantics before using these for authoritative remote
building or costs. The legacy SDK resource-capacity offset is not a valid
substitute. Filter/multiplayer coexistence remains untested.


## October 3 native alpha validation

The exact-build adapter now saves loaded sectors before metadata. Streamer
vtable slot +0x20 resolves to saveLoaded (RVA 0x222d30); +0x28 resolves to
asyncFlush (0x222570). SaveGame metadata and blueprints use 0x1d9e20 and
0x1d9a40. Copying the private profile happens on the game thread, then a
hash-checked immutable generation is published. Metadata-only saving failed
to preserve the remote ship and must not be used as a complete checkpoint.

The save/restart/rejoin test validates two existing 58-block command ships,
canonical faction IDs, block geometry, pose, and runtime health. Native startup
returns the existing-ship result for both seats. World coordinates wrap at the
native map size; comparison must use toroidal distances. The report deliberately
keeps fullCampaignMultiplayerValidated false: independent remote blueprints,
progression, edited designs, and distant-sector gameplay remain unfinished.

Debris identity failures had two causes: a cached command pointer could belong
to another cluster, or could point to a non-command seed block. Identity code
now checks Block.cluster (+0xb8) and COMMAND feature bit in Block.features
(+0x40); non-command fragments use their persistent block identities. Serialized
block order can change, so the strict scene parser scans block identities rather
than assuming the first serialized block is the tagged one.

The latest 120-second native test applied 468 updates, presented 28,589 frames,
and passed movement, firing, respawn, authentication rejection, queue pressure,
and disconnected-seat AI takeover. Measured p95 interval 5 ms, maximum 19 ms,
zero intervals over 50 ms. These are same-machine presentation intervals, not
proof of physical two-machine play quality or native remote control behavior.
Control pressure coalesced 26 commands with a peak pending count of two.

The standalone console-free Tk package passed extracted-archive construction,
native two-process transport, and native checkpoint smoke tests. Packaged
profiles/checkpoints live under LOCALAPPDATA/Repopulated; source state remains
under .runtime. Frozen external game spawning clears SetDllDirectoryW first.
The package includes its own Python/Tk/Frida runtime and no game assets.

Native control-mode research loads MOUSE_ROT, KEY_ROT, and CARDINAL saves and
captures AI::playerUpdate intent. Neutral MOUSE_ROT/CARDINAL dimensions are
0x104, versus KEY_ROT 0x8. This verifies saved mode parsing and finite intent;
it does not verify physical mode transitions, bindings, or remote native menus.
The custom bridge is not a substitute for those native semantics.

The next integration prerequisite is persistent cluster lifetime. Native
removeFromGameZone is RVA 0x8cf40; pool_free is 0x7e7d0 and asserts the cluster
is detached. The level_load handler at 0xbd1a0 appends clusters through
GameZoneAdd (0x133290); the alpha wrapper currently clears first. These are
read-only research leads, not verified incremental replication APIs. Player,
editor, targeting, children, and pooled reference ownership must be understood
before replacing full scene reconstruction with safe persistent updates.

## October 3 native presentation stream

`native/presentation.c` and `tools/presentation_wire.py` add bounded visual
state independently of authoritative cluster serialization. Limits: 4,096
weapon records, 2,048 ordinary projectiles, 512 thrust events per snapshot;
the native capture ring holds 4,096 events and uniformly subsamples a 300 ms
window. The TCP scene frame limit is now 4 MiB. Both peers require presentation
version 1. Validators check shapes, finite numbers, budgets, ownership scene,
unique weapon IDs, geometry, color integers, and ordered event times before
packing the exact native ABI (56/36/44 bytes per record).

Current-build code verifies EffectsParticleSystem::thrust at RVA 0x1cc440,
with packed float2 arguments and native Windows x64 calling conventions.
A C replaceFast hook forwards the original host call and captures parameters;
the client replays them into its own particle system. Projectile draw records
are appended through 0x171400 into GameZone +0x330 after projectile pass 0.
The stock pass 1 draws them. Client positions include the native loaded-sector
center; omitting this offset rendered particles and bullets outside the view.

Turret pointer is runtime Block +0x158; its first four floats are current angle
and RenderAngle previous/current/render. Laser pointer is +0x150; firing is
+0, render start +0x24, render end +0x3c, and hitting byte +0x45. The native
beam renderer is Block::renderEffect at 0x1f5e90. Renderer-time restoration uses
entity and persistent block IDs after each load, never retained native block
pointers. Weapon-only persistent IDs begin at 0x78000000 and preserve the
saved high-water mark. Non-command cluster identity uses the minimum persisted
block ID, matching the parser despite reordered fragment blocks.

Charging beams initially never fired under remote control. Block::fireWeapon
sets CHARGING (0x80000000), and chargingUpdate holds off laser firing while
that bit remains enabled. Native release clears owned weapon enables via
Block::setEnabled at 0x5dd20, leaving chargeTime and laser decay to the engine.
Remote held fire now runs in the native AI phase, with explicit release and
500 ms stale-input expiry. The native update signature and setter are verified
against this executable before use.

The 60-second combined beam/respawn/queue-pressure run in
`.runtime/coop-check-1791037695806321700.json` passed: 203 snapshots, 14,151
client presentation calls, p95 5 ms, max 20 ms, zero intervals over 50 ms;
98,173 thrust events, 10,892 projectile draw records, 3,869 turret applies,
4,102 native beam render calls, one respawn, queue peak two, and AI takeover.
These counters verify execution and transport, not complete pixel fidelity.
Windows Firewall's foreground permission prompt obstructs desktop captures;
the user will handle it when home. Clean host/client visual comparison and a
physical two-machine test remain outstanding. No firewall setting was changed.
Explosions, impacts, shields, and charging effect particles remain incomplete.
