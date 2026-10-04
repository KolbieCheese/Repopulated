# Engine integration: unresolved release gate

The project now has a limited [native two-player alpha](native-alpha.md), but
does **not** yet provide complete faction/campaign multiplayer. A
native diagnostic DLL has been compiled and exercised in the real game's
menu/demo, headless fixtures, and a fresh rendered campaign with a remote pilot.
See [native research results](native-research.md) for exact evidence and limits.
No full host/client game adapter or campaign checkpoint binding is provided.
Additional research now verifies headless sector save/restore, authenticated
TCP waypoint commands for independent empires, and pose replication between two
native game processes. Those fixed fixtures do not satisfy the production
engine contract: full-world streaming, general faction/fleet control, building,
and campaign restore remain missing.
The production server remains transport/session infrastructure. Its tests use
a protocol fixture; the separate alpha checks use actual native game processes.
Do not publish this as a complete multiplayer mod.

## Evidence

- [Official modding documentation](https://www.anisopteragames.com/docs/) describes
  typed data files with Lua-inspired syntax, not a Lua script interpreter with
  per-frame callbacks. Its listed extension points are blocks, factions, regions,
  shapes, audio, text, fonts, shaders, and cvars.
- The locally inspected Steam build is 19149099, Release64, compiled July 7, 2025.
  The x64 executable SHA-256 is
  `8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c`.
- `kHeadlessMode` and `kTournamentHeadless` exist in generated cvars and embedded
  strings. This does not establish support for a persistent campaign dedicated
  server. The shipped network functionality includes HTTP agent exchange.
- The shipped executable contains a developer PDB pathname; the actual PDB is
  not shipped in the installation. A pathname is not usable debug symbols.

`tools/inspect_install.py` performs a repeatable read-only PE/import/export and
active-mod log inspection. It never runs or edits the game. Reports and local
path specs go into ignored `.runtime/`.

## What the native adapter must do

There is a real AI DLL interface beyond the data documentation. The
[AI mod example](https://github.com/Akaito/reassembly-ai-mod-example) exposes
`GetApiVersion(int*, int*)` and `CreateAiActions(AI*)`, with faction `ainame`
selecting the DLL. Its game headers include `AI`, `GameZone`, `BlockCluster`,
`Player`, `SaveGame`, and serialization types. The inspected example commit is
`f69fe00c7a86dc589ceb4bb820bff3a6aa7606f8` (March 27, 2019), and its instructions
target x86/Visual Studio 2017. It is not a verified SDK for the installed 2025
x64 build. A reference clone is kept only in ignored `.runtime/ai-sdk`; its game
headers have copyright notices and have not been redistributed in this project.

The local x64 executable has **808 named exports**, including `GameZone::Update`,
`GameZone::getClusters`, `GameZone::getProjectiles`, `GameZone::getResources`,
`GameZone::insert`, `BlockCluster::addToGameZone`, `AI::update`, and
`SerialBlock::setFaction`. These make an adapter more plausible than a stripped
binary would, but they do not establish compatible C++ object layouts, safe
thread callbacks, or a complete campaign serialization API. In particular,
`Player`/`SaveGame` headers are not equivalent to callable exported controller
and checkpoint APIs. Do not bind the old headers blindly to the current engine.

Prefer validating and extending this native AI interface or an engine-source
integration. Build-specific reverse engineering/hooks may still be required. First
prove safe access to the simulation thread, player controller, active world,
serialization, entity lifetime, and engine save/load functions on this exact
build. Never guess function addresses or object layouts. Check the executable
hash and signatures before installing any hook and fail on unknown builds.

The implementation needs both sides:

1. A host adapter running the **actual Reassembly simulation** with multiple
   human controllers, faction ownership, AI, region streaming around all players,
   and authoritative world state. Headless viability must be tested independently.
2. A client adapter capturing controls, applying authoritative entity/block state,
   handling ship construction, damage, resources, projectiles, fleet commands,
   prediction/reconciliation, and rendering. A browser lobby client is not this
   adapter. Serialized full JSON worlds are a bootstrap contract; production
   transport needs measured binary/delta replication and interest management.
3. Reliable mapping between per-player empire IDs and game faction/relocation
   IDs, including two players choosing the same content faction. Replicating just
   ship positions or copying save files is insufficient.
4. Engine-level validation of input/build costs, tick scheduling and limits,
   checkpoint consistency, and migrations. The transport only validates envelopes.
5. Validation with two independent game processes: movement, collision, block
   destruction, editing, construction, AI, warps, distant sectors, reconnects,
   saves, and identical Workshop content. Existing single-player saves are not
   converted by this project.

Until these are implemented and tested, actual multiplayer remains blocked.

## Bootstrap protocol

TCP newline-delimited UTF-8 JSON, protocol version 1. Maximum frame 2 MiB.
Handshakes have a 5-second deadline. Ping/pong runs every 5 seconds; an idle
socket expires after 15 seconds. Queue and rate limits disconnect abusive peers.
The transport is plaintext; use only a trusted LAN or encrypted VPN during
development. Internet-ready deployment needs TLS and authenticated distribution.

Player hello:

```json
{"type":"hello","protocol":1,"role":"player","name":"Pilot","faction":8,"manifest":{"schema":1,"gameHash":"<sha256>","mods":[]}}
```

Optional `password` and `resumeToken`. Mods are ordered `{id,hash}` records. A new
player receives `welcome` with `playerId`, unique `empire`, `faction`, `sessionId`,
and a 256-bit `resumeToken`. Store the token privately. Server saves contain only
token hashes; the local launcher profile contains the reconnect secret. The
server sends `lobby`, `chat`, and (when an adapter supplies one) `snapshot` frames.
Ready: `{type:"ready",ready:true}`. Chat: `{type:"chat",text:"..."}`.

Engine hello is loopback-only, authenticated by the per-start secret in ignored
`.runtime/engine-credentials.json`:

```json
{"type":"hello","protocol":1,"role":"engine","token":"<secret>","gameHash":"<sha256>","manifestHash":"<canonical hash>","capabilities":["authoritative-world","player-input","multi-faction","checkpoint-restore"]}
```

`manifestHash` is SHA-256 of `JSON.stringify(validateManifest(manifest))` using
the canonical key order in `src/protocol.js`. Capabilities declare a contract;
they do not independently prove an adapter is correct.

Engine receives `engine-welcome`, then `start` after the host starts a ready
lobby. Both include settings, roster, session ID and latest checkpoint. Engine
must restore that checkpoint before acknowledging `started` with the session ID
and its complete initial `tick` and `world` checkpoint. The server persists it
before entering `running`. A 10-second start timeout closes the adapter.

Player `input` contains a monotonically increasing `seq` and a `controls` object
bounded to 16 KiB. The server supplies authenticated `playerId` and `empire` when
forwarding. A native adapter defines and validates the actual control schema.
Player ownership fields are ignored. Sequence resets on reconnect. Engine emits
`snapshot` with a strictly increasing `tick` and `world` object. Host saves the
latest complete checkpoint every 10 seconds, on manual save, and shutdown.
Snapshots are assumed to be complete authoritative checkpoints, not deltas.
No checkpoint means no actual gameplay state has been saved.

Adapter loss suspends a world. Resume requires a replacement adapter, ready
players, and restore of the saved checkpoint. Player seats remain reserved while
offline. Roster removals are limited to a pre-world lobby to avoid silently
orphaning saved ownership. Host settings lock once a world exists. `pause`,
`settings`, `player-connected`, and `player-disconnected` are engine notifications.

## Remaining product work

- Native in-game browser/overlay; this version has a separate loopback web UI.
- Verified content/game-data digest from the running engine, including base data,
  effective cvar overrides and Workshop relocation mapping. The current manifest
  fingerprints executable and ordered whole mod directories conservatively;
  it does not prove all effective engine data matches.
- Public directory registration with expiry and verification; browser now offers
  LAN broadcasts and direct TCP probes only. No public directory is hosted.
- NAT traversal/relay, encryption, lag compensation, spectator support and
  bandwidth/performance measurements.
- Dedicated **Reassembly** process orchestration and campaign headless testing.
  The included dedicated process currently hosts the transport, not the game.
