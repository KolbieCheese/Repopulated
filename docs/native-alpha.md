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
presentation stream now carries native thruster particles, ordinary projectile
trails, turret rotation, and beam firing/endpoints. The client uses the game's
particle system and weapon renderers; it does not simulate damage or projectiles.
Explosions, impact particles, charging glows, shields, and other effects still
need independent replication and visual verification. Scene delivery remains
four updates per second, but unchanged ships now persist. Pose, health,
resources, energy, growth and timers update in place; changed structures and
removed entities are replaced. Bounded linear/angular prediction and correction
smoothing present motion between snapshots. The client is paced at 60 FPS;
visual smoothness still needs a two-computer playtest.

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
update together: native protocol version 4 and presentation version 2
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
`.runtime/native-checkpoints`. Stop hosting, select that galaxy in the Host
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

This check launches and stops two private native game processes and sends
actual TCP drive/fire commands. It does not synthesize keyboard presses:

```powershell
python tools/native_coop.py check --exe D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe --seconds 180 --check-respawn --check-backpressure --check-beams --check-health --check-map-menus --check-host-menus
```

The latest separate-exploration 180-second native-client run applied 688 updates
and observed 10,733 presented frames (about 59.6 FPS across the run). The 95th
percentile frame interval was 17 ms, the largest was 22 ms, and no interval
exceeded 50 ms. Runtime pilot health matched across 688 snapshots and 38,528
block comparisons, with maximum error 0.000260. The test verified bounded input
coalescing without dropped controls, native effects, and one test respawn.
Native map/Binding open/close cycles passed on both sides: the client applied
eight snapshots during each two-second overlay, while the host exported
12 and 11 snapshots during its three-second overlays. The map readback matched
100 cells, region records and the remote's discovered objective markers
throughout the run. The server withheld undiscovered remote marker records.
The test-only beam fixture substitutes a stock laser in the private ships;
it never changes installed game content. It also verified movement, firing,
respawn, bounded control coalescing during a deliberate pause, and AI takeover
after disconnect. These short local results
do not prove sustained 60 FPS or smooth motion on a second machine.

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
