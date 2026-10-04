# Native gameplay integration

The two-player alpha now uses a native campaign client with its Player bound to
the authoritative remote command ship. The host advances physics, AI, damage,
resources and spawning. The client keeps native input and presentation while
local block/physics simulation and sector streaming are suppressed. Complete
ship editing, faction progression, menus and independent sectors remain required.

## Controls and menus

The installed build exports AI::playerUpdate at RVA 0x1ce8c0. Its navigation
path calls BlockCluster::getNavConfig and sNav::update. Navigation intent is
stored in AI at dimensions +0x2b8, velocity +0x2c4/+0x2c8, angle +0x2cc,
configuration +0x2d4. The client now transports the native player's resulting
six-value destination, four-value precision and dimension flags. Position
targets are relative to the controlled ship; angle and angular velocity are
retained. This uses native binding interpretation. Physical keyboard, controller,
rebinding and mode transitions still require hands-on verification.

The old SDK lists MOUSE_ROT, KEY_ROT, and CARDINAL control schemes, R to cycle
them, left/right/middle mouse for weapon groups, Space for target lock, Q/E
for strafing, X to stop, 1 for the constructor, 3/I for the ship selector,
M for the map, U for upgrades, and B for weapon bindings. These are research
leads, not proof of the installed build's complete behavior. Current controls,
mode transitions, targeting, weapon groups, and menu focus need current-build
verification. Opening a local menu must release flight/fire input without
pausing the shared host simulation. Native editor keys must never be consumed
by a network flight hook.

Native FiringData and AI::fireWeaponsAt provide an aim/fire path already tested
in the alpha. Capturing native weapon intent must also preserve weapon groups,
target locking, auto-aim, and firing gates rather than just an aim position.
The client captures native FiringData aim/target velocity/spread per weapon
block, then sends the native enable mask for that block. The host validates
every block against the owned ship before applying the request in its AI phase.
Unknown IDs after damage/respawn wait for fresh input. Native charging
weapons fire on release: the host must clear their weapon/charging enables
through Block::setEnabled, preserve chargeTime, and let chargingUpdate run.
Apply held fire in the native AI phase; firing before GameZone::Update can be
cleared before block simulation. Native groups/targeting and charge behavior
still need physical and visual parity tests.

## Presentation fidelity

A bounded native protocol-7 state stream carries ship motion, weapon block IDs,
turret angle, laser firing/endpoints, nearby ordinary projectile state, block
health/growth/lifetime, and native mover throttle values from one game update.
It carries both wall and simulation timestamps: native velocities are measured
per simulation second. Presentation accounts for their measured clock ratio.
World snapshot validation and planning run in separate processes so large Lua
scenes do not starve fast-state delivery.
The client generates exhaust locally through Block::moverUpdate (0xf09e0) and
the stock particle system. Particles are not transmitted. Its exhaust emitter
uses the same render correction as the source ship, preserving attachment while
authoritative poses change. The client invokes the stock weapon render paths
without running local weapon, collision, or damage simulation. Persistent
weapon block IDs are necessary: level expansion can recenter local block
coordinates, and respawn/fragmentation invalidates cached pointers.

This removes several static-ship symptoms but does not reproduce every native
effect. Add explosions, impacts, charge glows, shields, short-lived effect
events, and native resource visuals. Verify these visually on both machines.
Geometry delivery targets four Hz; state frames target 20 Hz independently.
Unchanged clusters persist; pose, health,
resources, energy, growth and lifetime update in place. Deleted or structurally
changed clusters use native removal/deferred-free and append paths. Structural
growth, fragmentation and launcher attachments can still replace an affected
ship. Older geometry cannot rewind retained fast-state poses. Native clients
default to a 100 ms source-time buffer with Hermite interpolation and wrapped
angles; missing brackets extrapolate for at most 250 ms, then freeze. The same
buffer selects health, weapon, projectile and mover presentation. The native
camera follows that presented pose. The added visible control delay remains
until verified immediate input replay is enabled. The client is paced at 60 FPS; longer tests and other
hardware must establish the actual experience. Native local movement prediction
is implemented experimentally using the mover helper and the game's velocity
integrator (0x15c6b0); it remains disabled in the regular launcher pending
hands-on rotation, latency, collision, and correction-quality testing.

## Persistent campaign client

Player::onContinue at RVA 0x1dd560 identifies the command block using
SaveGame.playerIdent (+0xd0). The global Player command pointer is +0xa8.
The client now removes individual roots with removeFromGameZone (0x8cf40),
killRecursive (0x7f150), and pool_free_mainthread (0x7e920), following native
Clear ownership rules. Level loading (0xbd1a0) appends additions. The verified
Player setter at 0x1dd170 rebinds the watched command after replacement.
The campaign SectorStreamer update (0x2230f0, vtable +0x10) is suppressed only
in the client process. Native control capture runs only for the bound player.

Focus loss/minimization sends neutral controls immediately. GSFly's active-state
check (0x119f90, observed from update 0x11c700) gates input while overlays are
active; the host also expires stale intent after 500 ms. The remapped constructor
paths (0x11a0e0 and 0x1d3100) are guarded because authoritative edit transactions
are not implemented. Unsynchronized Upgrade/Fleet tabs are excluded from the
native MessagesTab container (0x27fe90). GSList::step (0x118300) now supplies
an update-thread heartbeat while native overlays pause the usual flight update.
It applies client snapshots and releases flight intent while a menu is open.
On the host, it advances the authoritative zone and the normal network phases,
while a Player wrapper brakes the host ship and releases weapons. Updates from
a separate editor zone cannot consume campaign controls or export campaign
snapshots. Native map/Binding open-render-close cycles pass automatic checks;
physical input, editor and other menu transitions still need manual testing.

## Native map

GameZone::getMetaZone (0x5ebb0) exposes MetaZone at zone +0x250. The native
GalaxyMap contains radius at +0x10, width at +0x18 and a vector of 12-byte cells
at +0x20. Each cell supplies region ID, centrality and visited/valid bits.
The authoritative map stream carries these values and region colours/factions;
the replica preserves its own save-file validity bit and increments the native
map version at +0x38 when visible data changes. The existing minimap/full-map
renderer then refreshes its own texture.

Native markVisited (0xfea60) records remote exploration from its authoritative
ship pose on the host. Discovery is separate by default: the native method
receives a copied MetaZone prefix and a separate cell vector. The thread-scoped
Notifier hook (0x1cac40) suppresses only shadow discovery notifications, so the
host's singleton Player does not gain exploration progress for a remote faction.
Other native notifications delegate to the original routine. The radius matches
MiniMap's native setting: twice the float at image +0x3cf3e8 (caller 0xa77f0).

The client's render-time discovery call is suppressed so it cannot reveal cells
the server has not sent. Remote objective records outside its discovered cells
are filtered before transmission; common galaxy cells/regions remain consistent.
The host can explicitly opt into shared exploration, which merges existing
discovery. Turning it off does not erase previously learned knowledge.
Native readback validates cells, regions, policy and objective records for each
applied snapshot. Save/resume includes validated campaign-map.json,
remote-map.json and map-settings.json sidecars and checks both discovery records.

SaveGame's objective vector at +0xe0 supplies authoritative station/objective
markers. getWrappedPos (0x17c8b0) provides toroidal marker positions. The client
uses independent native MapObjective objects rather than linking markers to
extrapolated ships. It updates the native render copy through onZoneRender
(0x1ddb70) under the game's recursive save mutex at +0x1f0. Marker records carry
stable session keys, identities, factions, flags, positions, destinations and
radii. Retained marker references are bounded to 8,192 per process.

The current private campaign remains a limited two-seat fixture. Full native
galaxy bootstrap, complete per-faction progress, map command ownership,
multi-sector streaming and per-faction progress/territory rules remain required
before the map can measure complete multiplayer campaign success.

All RVAs above refer to the Windows x64 executable with SHA-256
8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c.
The sibling Reassembly-Research repository contains recovered assembly and
automatically generated pseudocode, not original buildable game source.

Vanilla gameplay also assumes a local player faction. The host's current
campaign ship is faction 100 while the remote authority faction is 20008.
Client-local faction views may need translation while server identities and
ownership remain canonical. This requires checking native faction predicates,
alliance logic, HUD, construction costs, and progression; rewriting a display
label is insufficient.

## Save and resume model

The server owns one galaxy and a record for each persistent faction. A player's
reconnect credential identifies their seat, independently of a transient
connection or the currently controlled ship. Joining a saved galaxy presents
available factions and the player's existing faction, rather than silently
creating a replacement empire. An existing seat retains its blueprint library,
selected design, children/fleet designs, current command identity, resources,
unlocks, progression, fleet settings, and faction colors/name. Controls and UI
preferences can stay local to each player.

AI operates a vacant faction until the server grants its control lease to a
player. The current alpha now performs this handoff for its single remote
seat. General faction leases and host departure/dedicated-server policy still
need implementation. A reconnect must never reset progress or duplicate ships.

A checkpoint must contain the native galaxy's loaded and unloaded sectors,
all faction records, simulation state needed for continuation, an ordered mod
manifest/content hash, and identity/ownership metadata. Write these as one
checkpoint generation and publish it atomically after all parts succeed.
Keep the previous generation. Loading incompatible or incomplete data must
produce an error, rather than starting a new galaxy.

The alpha now verifies SaveGame::writeToFile at RVA 0x1d9e20 and
writeBlueprintsToFile at 0x1d9a40. Metadata alone omitted the remote ship:
the native streamer must first save loaded sectors (0x222d30), then flush its
writer (0x222570). The implementation copies the resulting private profile on
the game thread and publishes a hash-checked immutable checkpoint generation.
The save/restart/rejoin test verifies both existing command identities,
factions, saved block geometry, position, velocity, angle, and runtime health;
startup reuses the loaded ships rather than spawning replacements.
Position comparison accounts for the native galaxy's toroidal coordinates.
This validates native alpha continuation. Remote edited designs, independent
blueprint libraries, unlocks, resources, and distant-sector gameplay still
need full multiplayer implementation and end-to-end verification.
tools/inspect_native_save.py reproduces the initial string-reference analysis
without reading normal player saves.

## Main menu and completion checks

The desktop launcher now hosts and joins without running an HTTP server.
A Multiplayer entry in the game's native main menu is desirable. It requires
verified menu construction, event dispatch, and ownership/destruction paths;
native UI injection must remain build-gated and independent of the other
addon in this repository.

Before calling the campaign implementation complete, verify both sides:

- Native control schemes, rebinding, aiming, weapon groups and focus loss.
- Enter/exit editor, ship selector, map, upgrades, settings and pause screens.
- Build a changed ship, apply it, spawn a child, and select another design;
  the host validates and replicates the resulting authoritative changes.
- Choose different factions on a new game, then save, stop both processes,
  load, reclaim both seats, and compare ships, progress and sector contents.
- Disconnect a player, observe native AI takeover, then reclaim the same
  faction without loss, duplication, or another player's ownership.
- Measure presentation intervals and visible motion on two physical machines,
  including editing, combat, respawn and packet loss. Short same-machine
  automated runs do not establish a polished multiplayer experience.
