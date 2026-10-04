# Native multiplayer test — October 4, 2026

Extract the updated `Repopulated-Multiplayer-Alpha.zip` on **both Windows PCs**.
Run `Repopulated Multiplayer.exe` with its `_internal` folder beside it. Select
each PC's installed `win64/ReassemblyRelease.exe`. This adapter requires the
verified executable hash and stock content; SteamOS and enabled mods are not
supported by this build.

On the host, choose **New galaxy**. Leave **Share exploration between players**
unchecked for separate faction discovery (the default), or check it to share
exploration. Then choose **Host game**. Share the host LAN address,
game port and join token. On the joining PC, refresh LAN servers or enter the
address, port and token. Leave **Native campaign controls and HUD** checked,
choose an initial rotation mode, and click **Join game**. Use the same native
game controls you normally use. Each PC runs its own installed game; the host
alone simulates the shared world.

This build uses a 100 ms presentation buffer to smooth the client's movement
and align its weapon/damage visuals. Expect some visible response delay while
immediate local input replay remains disabled. Both launchers must use native
protocol 7; update both together.

## Flight and combat

1. Check that the joining player has the campaign HUD, a following camera and
   animated thrusters, projectiles, turrets and beams rather than static ships.
2. Fly, aim and fire on both PCs. Check mouse rotation, keyboard rotation and
   cardinal rotation, including the game's mode-cycle shortcut. Test strafe,
   braking, primary/secondary groups and target lock using your own bindings.
3. Release movement and firing, then alt-tab while holding them. The remote
   ship should stop receiving held input; stale commands expire after 500 ms.
4. Zoom and resize the joining game. Check that aiming follows the viewport.
5. Open/close pause and settings, the map and weapon Binding screen on each PC.
   Confirm flight/fire input stops while a menu is active and returns after
   closing it. The other player should still move and receive world updates.
   Automated native tests exercise map and Binding on both sides; pause and
   settings still need a hands-on check.
6. Observe damage, debris, missile launches, ship destruction and respawn.
   Watch for camera jumps, lost control, frozen effects or long frame pauses.

The joining constructor, Upgrade and Fleet workflows are guarded: authoritative
build/progression transactions are not implemented yet. Attempting their
shortcuts should produce an explanation in the launcher. Do not test remote
building or host ship switching as a supported workflow in this version.
Stay near the host; the host still determines which sectors are loaded.

## Map and exploration

1. Compare the native minimap and full map on both PCs. In areas both players
   have explored, galaxy layout, region colours/factions and shared visible
   station/objective data should agree. Each faction has its own discovery by
   default, so their fog and explored percentages can differ. Each player's
   local camera/player arrow can also differ.
2. Explore nearby areas, then open the joining player's map. Confirm it keeps
   updating while a visible marker changes. Remote discovery should not reveal
   the host's unexplored areas. Repeat with the host's map open while the joining
   player moves. With sharing enabled, both sides should learn newly revealed
   areas and show matching discovery.
3. Check that closing a map restores flight input without held fire.
4. Note the explored percentage in the launcher. Save, restart and rejoin,
   then compare the map again; previously explored cells should remain visible.

Selecting a saved galaxy restores its sharing checkbox. Choose a different
setting before hosting to change the policy. Disabling sharing preserves
knowledge already acquired while it was enabled. Older shared-only checkpoints
also retain their previously learned areas for both seats.

The alpha does not provide synchronized map clicks/autopilot, territory scoring,
or a verified complete vanilla galaxy campaign. Region cell counts describe
the native map; they are not a conquest or player success score. The current
private campaign remains a limited two-seat fixture.

## Continue the same galaxy

1. While both players are connected, click **Save current galaxy** in the host
   launcher and wait for **saved**.
2. Stop joining and hosting. Select that saved galaxy in the Host tab and host
   it again. Share the new join token and reconnect from the other PC.
3. Check that both ships, their damage, each faction's map exploration and the
   sharing setting survive, and that
   the remote player reclaims the existing ship rather than creating a duplicate
   faction.
4. Disconnect the joining player briefly. Its faction should fall back to AI;
   reconnecting should reclaim the same remote seat.

These checkpoints retain the host campaign and both current faction ships.
Full remote blueprint libraries, unlocks, progression and faction selection
are still missing. Keep `%LOCALAPPDATA%/Repopulated/native-checkpoints` when
updating the standalone launcher. Source runs use `.runtime/native-checkpoints`.
Save before leaving the host's campaign. Returning to the game's main menu ends
that multiplayer session; start hosting the saved galaxy again to continue.

## Report a problem

Keep the launcher open long enough to note any error, then use Stop joining /
Stop hosting. Describe which side was affected, the rotation mode, the action
immediately before it happened, and whether it reproduced. A short recording of
both games is especially useful for aiming, animation and stutter problems.
Session `instrumentation.json`, `native-telemetry.jsonl`, and the private game's
`Reassembly/data/log_latest.txt` are stored in the newest `native-host-*` or
`native-join-*` folder under the applicable state directory. Do not share join
tokens or unrelated profile/credential files.

Automated tests exercise the two-game TCP/native API path. They substitute
navigation/weapon intent at the network boundary and do not prove physical
input, complete menu behavior, two-PC latency or sustained 60 FPS on your client.
