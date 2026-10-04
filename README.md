# Repopulated

An experimental multiplayer project for **Reassembly**.

**A native two-player flight/combat alpha is now available to try.** It runs a
host campaign and a native campaign client, with server-authoritative movement
and combat, nearby scene replication, camera following, respawn, and alpha
save/resume. The joining game uses its own native Player, controls and HUD.
It sends navigation and per-block weapon intent to the host, including angular
velocity for keyboard rotation. Persistent replica objects, correction smoothing,
and native particles, projectiles, turrets and beams improve client presentation.
The native map now receives host exploration, region colours/factions and
station/objective markers. Each faction explores separately by default; hosts
can opt into shared exploration. Both discovery records survive alpha save/resume;
map and Binding overlays keep world updates moving on both sides.
See [native alpha setup](docs/native-alpha.md).
The automated native control/render check passes; hands-on and two-computer
playtesting remain outstanding.

The complete faction/campaign multiplayer mod is still unfinished. Remote
building, full per-faction progression, mod support in the native adapter, and a dedicated
campaign simulator remain missing. The separate transport/lobby infrastructure
has a server browser, settings and persistence, but its production Start action
remains blocked until a complete engine adapter satisfies its contract.

```powershell
python tools/native_desktop.py
```

The desktop window combines Host, Join, LAN discovery, and saved alpha galaxies.
It uses private profiles and stock ships, leaving normal saves and mods alone.
See [native gameplay integration](docs/native-gameplay-plan.md) for the remaining
ship editing, faction choice, progression, and remaining native UI work.
Use the [evening playtest guide](docs/native-evening-test.md) to check this build.

## Implemented and tested

- TCP sessions with bounded framing, protocol validation, deadlines, heartbeats,
  reconnect tokens and server-assigned empire ownership.
- LAN server discovery and direct-address probes in a loopback web launcher.
- Lobby roster, readiness, chat, optional passwords, and pre-game seat removal.
- Host settings: human faction/player slots, AI faction count, allowed base
  factions, seed, friendly fire, empty-server pause, and simulation tick rate.
  These settings are delivered to a future adapter; they do not configure the
  installed game today.
- Ordered SHA-256 mod manifests and executable fingerprints. A mismatch prevents
  joining. Whole mod directories are conservatively hashed, including metadata;
  the current fingerprint is not a complete effective-engine-data attestation.
- Atomic versioned session saves, previous-file backups, hashed server-side
  reconnect credentials, and an opaque authoritative-checkpoint contract.
  Tests exercise checkpoint restore with a protocol fixture. No Reassembly saves
  are imported or modified.
- A standalone dedicated **transport** process with a loopback host controller.
  A dedicated Reassembly campaign simulator is still required.
- A read-only installation/PE/active-mod probe and documented native integration
  findings. Native AI DLL hooks and exported C++ engine functions exist, but the
  available old example headers are not verified against this installed build.
- A native diagnostic DLL and live research harness, verified inside the actual
  game's menu/demo simulation and a headless two-ship sandbox. The headless run
  completed 601 simulation updates, read advancing ship positions, and verified
  a velocity change to a second command ship. This does not yet implement campaign multiplayer.
- A native sector save/restore check across two isolated game processes. It
  verifies two reconstructed command ships, factions, positions, velocities and
  continued motion. Whole campaign restoration remains unimplemented.
- Research TCP clients driving native faction waypoints, including distinct
  empires using the same base-faction ship, with ownership and replay checks.
- Two actual headless game processes exchanging ship poses over local TCP.
  Position, velocity and angle application are checked by native readback.
  Initial native serialized ships and block payloads are transferred over TCP
  and reconstructed. Complete combat state and campaigns remain missing.
- Native weapon firing with vanilla AI suppressed and a byte-identical ship
  serialization/reconstruction check between separate processes.
- Persistent ship IDs preserved through level reconstruction, replacing
  faction-only pose selection. Unidentified debris can receive a persistent
  first-block ID in the native research scene.
- Live TCP cluster snapshots reconstruct fired missile entities, removals, and
  an injected destroyed thruster. Native reserialization verifies entity IDs,
  poses and block topology. Some transient fields change during reconstruction;
  this is still a headless research fixture, not complete combat replication.
- Authenticated research TCP weapon inputs reject cross-faction commands and
  replays. Nonlethal thruster damage survives reconstruction; native runtime
  health readbacks match per-entity health multisets across both processes.
  Bounded semantic fidelity reports expose changed missile and AI fields rather
  than treating matching geometry as complete combat synchronization.
- A normal campaign host and a rendered remote native game exchange controls
  and bounded nearby cluster snapshots. Native thruster navigation, firing,
  client frame presentation, remote ship drawing, and command-block destruction
  followed by respawn pass the thirty-second native check. A separate loopback
  alpha launcher supplies host/join controls and LAN discovery for this mode.
- Native visual-state replication uses persistent weapon block identities,
  bounded particle event replay, the stock projectile draw cache, and the stock
  beam renderer. Held/released firing runs in the native AI control phase so
  charging weapons work. The 60-second presentation/respawn/queue-pressure
  check passes locally; complete effects and two-machine visual parity remain
  unverified. Both players must update their alpha launcher together.

## Try the infrastructure

Requirements: Node.js 22 or later. Python 3.9 or later for installation inspection.
There are no npm dependencies to install. Commands run from the repository root;
configuration paths resolve relative to that directory.

On Windows, discover the local installation and infer the last launch's mod order:

```powershell
python tools/inspect_install.py --out .runtime/install-report.json --mod-spec .runtime/mods.json
```

Review `.runtime/mods.json` against the **current enabled mods and order** in
Reassembly before generating a manifest. The probe reads the most recent game
log, which can be stale. Pass `--game-dir` and `--save-dir` for nonstandard paths.
For another platform or local mods, create a spec manually:

```json
{
  "gameExe": "D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe",
  "mods": [
    {"id": "workshop:123", "path": "D:/SteamLibrary/steamapps/workshop/content/329130/123"},
    {"id": "local:my-faction", "path": "C:/Users/you/Saved Games/Reassembly/mods/my-faction"}
  ]
}
```

Specs resolve relative paths against the spec's directory. Empty `mods` explicitly
means no active mods. Hashes do not upload or transfer game/mod files.

```powershell
node bin/repopulated.js manifest --spec .runtime/mods.json --out .runtime/manifest.json
node bin/repopulated.js server --config config/server.example.json
```

Open `http://127.0.0.1:32914` on the host. Find LAN servers or add
`127.0.0.1:32913`, choose a base faction and join the lobby. The Start button is
disabled because a native game adapter is absent. Do not copy this project into
Reassembly's Workshop/local-mod directory; it currently runs separately.

On a second computer with the same project and its own matching manifest:

```powershell
node bin/repopulated.js browser --manifest .runtime/manifest.json
```

Add the host's LAN address with TCP port 32913. Discovery uses UDP 32915.
Firewall rules and NAT forwarding are not changed automatically. The host web
controller binds only to `127.0.0.1`, on HTTP port 32914. LAN advertisements are
verified with TCP probes. There is no public directory, NAT traversal or relay.

Set `REPOPULATED_PASSWORD` in the process environment to require a join password.
Transport currently uses plaintext TCP, so development testing belongs on a
trusted LAN or encrypted VPN. Keep `.runtime/client-profile.json` and
`.runtime/engine-credentials.json` private; they contain credentials. On Windows,
filesystem privacy follows your user-directory ACLs; POSIX file modes alone do
not enforce Windows ACLs.

## Persistence and hosting

Restarting with the same configuration/save path restores session IDs, settings,
reserved empires and the latest adapter checkpoint. Rejoin through the same
address to reuse the launcher's locally saved identity. Offline seats remain
reserved. The host can remove them before a world is created. A new session at
the same address requires forgetting the old identity in the launcher.

The checkpoint is a complete opaque world object supplied by a future engine
adapter. Lobby-only saves contain **no game world**. Existing single-player
campaigns cannot be resumed as multiplayer games. Saves reject content mismatches
and invalid data instead of silently overwriting them. A `.bak` file retains the
previous save; recovery is manual while the server is stopped.

`server --no-web` runs without the browser controller. Ctrl+C shuts down and saves.
Use a separate save path and ports per transport instance. No service is installed
or scheduled, and no public server is launched by this repository.

## Validation and next release gate

```powershell
npm test
python -m unittest discover -s test -p "*_test.py"
```

The socket integration suite tests identity recovery, content rejection, engine
authentication, ownership checks, readiness, checkpoints, invalid traffic, LAN
discovery and browser authentication. Fixtures are explicitly not Reassembly.

[Engine integration findings and protocol](docs/engine-integration.md) describe
the unresolved native work. The next release gate is a verified adapter on the
installed game build, followed by two actual game clients playing and resuming
one shared galaxy. There is no completed gameplay validation or playable release.

[Native research results and reproduction commands](docs/native-research.md)
record the successful real-engine diagnostic and the unsuccessful headless
experiments. Diagnostic binaries, tools, game logs and test profiles stay in
ignored `.runtime/`.

This is an unofficial project, unaffiliated with Anisoptera Games.
