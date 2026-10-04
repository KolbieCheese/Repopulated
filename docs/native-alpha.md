# Native two-player alpha

This is the first interactive flight/combat prototype. It runs a normal native
Reassembly campaign for the host and a native campaign replica for one remote
pilot. Both ships live in the host's simulation. The client sends native intent and
receives the nearby ships, missile clusters, debris, and serialized damage.

It is separate from the production lobby/engine contract. It does not yet offer
complete faction multiplayer, remote building, progression UI, mod loading,
a public server directory, or a dedicated campaign simulator. Native alpha
save/resume preserves the host campaign and both existing faction ships; full
remote blueprint libraries and progression are not implemented yet.
The remote pilot must stay within the area loaded by the host. A separate
state stream now carries ordinary projectile trails, turret rotation, beam
firing/endpoints, runtime damage, and thruster control values alongside ship
motion, with host wall-clock and simulation-clock timestamps. The client generates exhaust every local game update through the
native mover/particle routines. Individual exhaust particles are not networked.
The client uses the game's weapon renderers; it does not simulate damage or
projectile collisions.
Explosions, impact particles, charging glows, shields, and other effects still
need host-confirmed triggers, local presentation, and visual verification. Scene delivery remains
targeted at four updates per second, but unchanged ships now persist. Pose, health,
resources, energy, growth and timers update in place; changed structures and
removed entities are replaced. A 100 ms source-time interpolation buffer presents
motion between state frames, targeted at 20 Hz. Weapon, projectile and damage
presentation select the same delayed frame; cosmetic mover values interpolate
between accepted frames on that source timeline. Source velocity uses simulation
seconds; presentation accounts for the measured simulation pace. This buffer adds
visible control delay; immediate local input replay remains experimental and off.
Input is
sampled at 30 Hz, with additional native weapon press/release transitions.
Large scene decoding and planning run in separate worker processes, so Lua parsing
cannot stall motion delivery through Python's shared interpreter lock. Older
geometry frames do not rewind roots already on the fast state stream. The stock
camera receives the presented pose before following it. The client is paced at 60 FPS;
visual smoothness still needs a two-computer playtest.
Scene imports use a private native Console field. They keep the live campaign
streamer visible to the independent renderer throughout ship additions. Native
motion and geometry also use the same ownership definition: an owned command's
serialized faction, or neutral zero for a commandless fragment. Temporary native
faction caches do not grant network ownership.
Regular native clients now commit scene changes between completed renders.
Verified same-command replacements retain their motion history. Commandless
roots can retain it only when their unique minimum persistent block identity
and native ownership survive the replacement; actual removals still reset it.
When prepared motion has waited at least 75 ms, the update thread can request
a slot after the next completed render. An unclaimed slot has a 2 ms requested
wait budget; a claimed scene transaction retains the normal commit safety
rules. This prevents repeated missed idle windows from starving fresh motion.
It does not remove the 100 ms presentation buffer or make native Lua imports
free of occasional frame-time spikes.

## Start on this computer

For the standalone Windows package, extract
`.runtime/releases/Repopulated-Multiplayer-Alpha.zip` and run
**Repopulated Multiplayer.exe** inside its folder. Keep the accompanying
`_internal` directory beside the executable. It bundles Python, Tk, Frida, and
the multiplayer DLL; no separate Python installation is required.

From the repository root:

```powershell
python tools/native_desktop.py
```

Choose **Host game** in the desktop window. The launcher shows
the game port and a generated join token. Share these and your computer's LAN
address with the remote player. **Find servers** also lists local hosts. The
server browser does not broadcast join tokens. Both host and join controls are
in one window; it starts no HTTP server.

On the other computer, run the same launcher, choose the server or enter the
host address, enter its join token, and choose **Join game**. Both sides must
update together: native protocol version 7 and presentation version 2
reject older alpha clients. Keep **Native campaign controls and HUD** checked.
Choose the initial **MOUSE_ROT**, **KEY_ROT**, or **CARDINAL** mode. The game
handles bindings, mode changes, aiming and weapon groups; navigation and
per-block weapon intent are sent to the host for authoritative execution.
Space retains its native meaning. Losing focus releases controls; the host also expires
stale movement inputs after 500 milliseconds. The camera follows the remote
ship. The host uses normal campaign controls. The two ships have separate
faction IDs and are not configured as an allied team. Physical control transitions,
aiming, target lock, charging weapons and remapped/controller bindings need
hands-on verification. Unchecking the native option uses the older WASD/mouse
sandbox bridge for diagnosis.

The remote ship respawns near the host after its command ship disappears.
Rejoining reuses the single remote seat and its current ship. Host-side normal
campaign ship editing exists, but remote editing and changes to the host's
selected ship are not a verified multiplayer workflow. The native client guards
constructor shortcuts and excludes unsynchronized Upgrade/Fleet tabs. Its
launcher explains attempted blocked actions. Native map and Binding overlays
now continue receiving snapshots; opening a host overlay also advances the
authoritative world and releases the host's movement/fire input. Settings and
other menu transitions still need manual testing. When a remote player
disconnects, its native AI resumes; rejoining reclaims the same remote seat.

The native minimap/full map receives authoritative galaxy cells, region
colours/factions and discovered station/objective records. Each faction has
separate discovery by default. The host computes the remote faction's discovery
from its authoritative ship position; this does not reveal host cells or credit
the host's exploration progress. The client cannot reveal extra areas locally.
In regions both have discovered, they see the same underlying galaxy data.

Check **Share exploration between players** on Host before starting to combine
discovery. Checkpoints preserve the host map, remote map and sharing setting;
selecting a saved galaxy restores the checkbox. Changing that option affects
future discovery and does not erase knowledge already learned under sharing.
Older shared-only saves retain their past discovery for both seats.

Map replication is checked by native readback, including while overlays are
open. The launcher displays each side's exploration percentage and marker count.
This is a limited two-seat campaign fixture, not a verified full vanilla galaxy
with complete progression, conquest scoring or synchronized map navigation.

The game host binds to all
interfaces on TCP 32916, and LAN discovery uses UDP 32917. Direct joining works
without LAN discovery. Discovery across routers and internet NAT traversal are
not provided. This prototype uses a plaintext token-authenticated transport;
it is intended for a trusted LAN. Use **Stop hosting** or **Stop joining** to
stop a session. Closing the desktop launcher stops its owned sessions.
The old optional web launcher remains available via tools/native_launcher.py.

Source launches use a new redirected profile under `.runtime`; the packaged
launcher uses `%LOCALAPPDATA%/Repopulated`. Normal save
slots, enabled local/Workshop mods, and any separately running game are left
alone. **Save current galaxy** creates a separate checkpoint generation under
`.runtime/native-checkpoints` for source launches or
`%LOCALAPPDATA%/Repopulated/native-checkpoints` for the standalone package.
Stop hosting, select that galaxy in the Host
tab, and host it again to continue; joining players use the new join token.
Saving flushes native loaded sectors, campaign metadata, and blueprints, copies
them on the game thread, and publishes a generation with file hashes. Loading
rejects incompatible or changed checkpoint files. Incomplete saves are not
listed. Keep the `native-checkpoints` directory in the applicable state folder
when updating the launcher or repository.
Do not copy this project into the game mod folder.

The launcher can be started with `tools/Start-NativeMultiplayer.ps1`. Use
`-GameExe` for another installation, or pass `--exe` directly to Python. Only
the verified Windows x64 executable is supported:

```
8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c
```

## Another computer's setup

The standalone package is the easiest Windows setup: extract the same ZIP,
run its executable, and choose the matching installed game. SteamOS is not
supported by this Windows native adapter.

For running from source:

Requirements: a matching installed Reassembly build, Python 3.9 or newer, and
Frida 17.21.0. Install Frida in that computer's Python environment with
`python -m pip install frida==17.21.0`. The local ignored Python tooling folder
is also supported. Compile the diagnostic DLL from this repository with an
official Zig 0.15.2 compiler:

```powershell
python tools/build_native.py --zig C:/path/to/zig.exe
python tools/native_desktop.py --exe C:/path/to/ReassemblyRelease.exe
```

The transport checks the executable and the stock Lua content fingerprint.
This mode deliberately uses fresh profiles without enabled mods; mod support
in the production lobby does not make the native alpha mod compatible yet.
Game files are not redistributed by this repository.

## Verification

The latest October 4 checks used both neutral-root continuity and the bounded
motion handoff. The two-minute moving-flight comparison matched camera/zoom and
capped the host at 60 Hz. The client displayed 7,104 frames and applied 394
geometry updates. Both sides had 17 ms intervals at the 95th percentile; the
client maximum was 29 ms and the host maximum was 18 ms. Neither recorded an
interval over 50 ms. All four requested handoffs were claimed, and the gate
completed 4,088 transactions without a timeout. All 963 neutral replacements
and four owned-command replacements retained their source motion history,
with no rejected tickets or unannounced resets. All 283,395 local exhaust
emissions had valid curves.
Report: `.runtime/live-controls-result-1791113407392586100.json`.

The subsequent three-minute combat regression passed movement, firing, beams,
damage, respawn, both sides' Map/Binding overlays, control coalescing and vacant
AI takeover. It applied 534 geometry updates and displayed 10,699 client frames
(about 59.4 FPS including startup). Client intervals were 17 ms at the 95th
percentile and 23 ms maximum, with none over 50 ms. The uncapped host's maximum
was 20 ms; its frame rate is not a matched comparison. All 29,902 compared
runtime pilot health values matched their selected authoritative frame. The
gate completed 6,051 transactions without a timeout; its longest transaction
was 16.011 ms and longest overlapping render wait was 14.561 ms. Four handoff
requests were claimed across seven reservations; three unclaimed reservations
expired safely. The longest actual reservation wait was 3.159 ms: the 2 ms
requested timeout is subject to Windows scheduling. All 4,192 neutral and 20
owned-command replacements retained their source curves without rejected
tickets or unannounced resets. All 777,487 local exhaust emissions had valid
curves, with no stale, missing, publication or invalid-root skips.
Report: `.runtime/coop-check-1791113791155426300.json`.

Sparse admission events retain every successful-acceptance gap of at least
150 ms. In the final combat run, the three such gaps were 217 ms during the
client Binding overlay, 367 ms across respawn, and 309 ms across a host menu
transition. All other gaps were below 150 ms; the largest logged ordinary-play
gap was 141 ms, with oldest prepared motion waiting 103.535 ms. The moving
run's largest post-loading pending age was 93.974 ms. Lifetime pending maxima
also include about 1.65-1.68 seconds while the first scene loaded. New reports
retain that honest lifetime maximum and separately expose
`bootstrapPendingAgeMs` and `longestActivePendingAgeMs`. These are delivery
measurements, not end-to-end physical-input latency.

An earlier combat regression had 15 intervals over 50 ms and a 98 ms maximum.
Owned wire-buffer conversion, geometry preparation before the transaction,
bounded native vector/block reads, and suppressing repeated diagnostic scene
scans reduced those costs. A later audit found prepared motion waiting 405 ms
for an idle scene slot; the bounded handoff addresses that scheduling gap.
The latest combat capture starts 100 seconds after native readiness and records
75 seconds, covering the late phase where earlier pauses occurred. These are
repeat scenarios in nondeterministically evolving galaxies, not identical
scene replays. Root continuity counters do not certify every attached child's
independent motion. Native Lua loading and blueprint fields remain intact and
can still cause brief import hitches. These short local results establish
improvement, not complete cosmetic parity or performance on a second machine.

The captures show both native game windows and are retained under
`.runtime/smoothness-recordings`. The recorder keeps the latest test and the
latest successful comparison, protecting unfinished captures. Video capture can
drop frames: comparisons record approximately 40–42 FPS despite the native game's
roughly 60 Hz cadence. Recordings cannot certify every rendered frame.

Native health comparison covers stable pilot block IDs common to the selected
damage frame and current geometry. Geometry membership is verified separately;
the health comparison excludes blocks awaiting addition or removal. Exhaust
origins now evaluate the source curve at each local native emission, and mover
throttle interpolates between accepted visual frames. Exhaust appearance still
differs during turns; native frame cadence alone does not prove cosmetic parity.

Immediate local pilot movement is experimental and disabled in the regular
launcher. It uses native thrusters and the native velocity integrator, then
rebases unacknowledged movement against host state. A separate 45-second
matching-input fixture ran 2,411 prediction steps and 744 reconciliations, with
a maximum positional correction of 27.853 world units; frame intervals were
17 ms at the 95th percentile and 22 ms maximum. This is not proof of physical
control or collision parity. A bounded manual test is available using
`tools/check_native_live_controls.py --exe <path> --seconds 180 --predict-local`.
Its `--test-controls` option drives native navigation internally and must not
be described as a physical keyboard test. Report:
`.runtime/live-controls-result-1791084556910010000.json`.

This check launches and stops two private native game processes and sends
actual TCP drive/fire commands. It does not synthesize keyboard presses:

```powershell
python tools/native_coop.py check --exe D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe --seconds 180 --check-respawn --check-backpressure --check-beams --check-health --check-map-menus --check-host-menus --record --record-seconds 75 --record-delay-seconds 100 --scene-idle-gate
```

The optimized combat run used independent exploration and verified native map
readback for 100 cells, region records and the remote's discovered objective
markers. The client applied six snapshots during its Map overlay and nine
during Binding; the host exported 11 and 12 snapshots during its overlays.
The test-only beam fixture substitutes a stock laser in the private ships and
does not change installed content. The input test deliberately pauses native
consumption for four seconds, then verifies bounded coalescing without dropped
controls. One real respawn was verified; its new command identity resets motion
history, while same-command structural replacements retain their source curve.

Additional native checks:

```powershell
python tools/check_native_coop_resume.py --exe D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe
python tools/check_native_control_modes.py --exe D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe --remote
```

The latest save/restart/rejoin check preserved both owned ships without
recreating them. Both 58-block ships retained their geometry and runtime health;
position error was at most 0.002 world units in the latest native-client run.
With separate exploration at the stock native 20,000-unit discovery radius,
the private remote probe increased its explored cells from 44 to 80 while
leaving the host's 44 cells unchanged. All 80 remote cells survived save/rejoin.
With sharing enabled, both factions received the combined map and the setting
survived save/rejoin. This
probe temporarily moves a test ship and restores it on the native update thread;
it verifies discovery isolation and persistence, not distant-sector gameplay.
The mode check loads MOUSE_ROT, KEY_ROT, and CARDINAL in actual native campaign
processes and captures native navigation intent. With --remote it also checks
all three modes in TCP host/client sessions. Its movement/fire fixture substitutes
intent at the network boundary: it does not press physical keys or prove the
complete aiming/menu experience.

These measurements establish the native network/control/render loop and
respawn. A hands-on keyboard and two-computer LAN playtest remains necessary
before calling the alpha fully playtested. The reports keep
`playableMultiplayerValidated` false for that reason; the production adapter
capabilities remain blocked until their complete contract is implemented.

Pure protocol/browser infrastructure checks:

```powershell
python -m unittest discover -s test -p '*_test.py'
npm test
```

The browser enforces its local Host, Origin, and per-launch session token.
Game controls are bounded, rate limited, monotonic, and assigned to the remote
faction by the server. Bad authentication/content/input closes that peer rather
than stopping the host. The scene parser rejects executable Lua syntax and
checks payload hashes, identities, root counts, and remote ownership before
handing a snapshot to the native parser. This is still an experimental native
adapter, not a hardened public game service.
