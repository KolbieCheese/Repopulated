# Reassembler Expanded

A separate **local Reassembly add-on**, independent of Repopulated multiplayer.
Adds large square utility blocks and copies technology from installed faction
mods into the existing Reassembler faction. No new faction or galaxy is created.

## Play

Installed location on this computer:
`C:\Users\maste\Saved Games\Reassembly\mods\reassembler-expanded`.

Restart Reassembly, open **Mods**, and enable **reassembler-expanded** alongside
**Reassembler**, **The Ingelwinn Authority**, and **ExtraReassembly**. Keep the
expansion at the top of the list (highest priority). Load your existing
Reassembler save and look in the Data Bank / block palette for **Square Reactor
XL** and **Square Resource Vault XL**. The game still applies its normal block
unlock/upgrade rules; your Free Block Upgrades mod can affect those rules.

## Default utility blocks

| Property | Square Reactor XL | Square Resource Vault XL |
|---|---:|---:|
| Footprint | 80 × 80 (8 × 8 ordinary hull squares) | 80 × 80 |
| P cost | 3,000 | 1,500 |
| Power generation / second | 10,000 | 0 |
| Power storage | 30,000 | 0 |
| Resource storage | effectively zero | 30,000 R |
| Mass | 1,920 | 1,280 |
| Health | 9,600 | 12,800 |
| Density | 0.30 | 0.20 |
| Durability | 1.50 | 2.00 |
| Regrowth speed (`growRate`) | 8 | 8 |

Both shapes have eight attachment ports per edge at ordinary hull spacing.
The reactor has a configured destruction blast of 1,500 damage / 100 radius.
The vault stores resources; your ship's existing tractor collects them.

`growRate` controls reconstruction/growth speed, measured in the game's radius
units per second. It is not an independent health regeneration field. Damaged
block healing follows the game's normal rules; this add-on does not change the
global healing cvars. Health and mass scale with area × durability/density.

Seventeen reactors provide 170,000 power/sec and 510,000 power storage for
51,000 P, before counting the ship's other blocks. A ship needing that much
continuous power will still devote a substantial part of its budget to reactors.
Output and stored power are different statistics; compare sustained weapon
demand with output when tuning your ship.

## Modded technology

The current build imports **1,267 buildable blocks** from Ingelwinn Authority
and ExtraReassembly, with **139 custom shapes**. Another 282 definitions remain
hidden to preserve inherited/projectile/drone references. Source faction blocks
remain available to their original factions. Reassembler's original blocks and
existing ships keep their original identifiers.

The generator resolves includes, block inheritance, replicated blocks, numeric
shape references, mirrored shapes, and embedded decoration shapes. It preserves
weapon statistics rather than balancing those mods again. Command cores,
environment blocks, roots/seeds and explicitly hidden parts stay out of the
palette by default. Set `includeCommandBlocks` to `true` to expose source command
cores too. Source command factions are rewritten for Reassembler ownership.
Blueprint names for spawned drones still refer to the original source mod's
ships, so keep source mods enabled. Specialized drone/launcher behavior has not
been exhaustively tested in combat.

This is a generated compatibility snapshot, not a runtime scanner. After adding
another mod, add its Workshop directory ID to `sourceWorkshopIds`, launch the
game once with it enabled so its relocation is recorded, and rebuild. There is
no guarantee every future mod's syntax/features will be supported; unsupported
references stop generation with an error. Enabled mods are inferred from the
latest launch log, which can be stale.

## Configure / rebuild / install

Python 3.9+; no Python dependencies are required to build or install.
From the Repopulated repository root:

```powershell
python addons/reassembler-expanded/build.py --install
```

Edit `addons/reassembler-expanded/config.json` to change output, storage,
cost, density, durability, regrowth speed or reactor explosion settings. Then
run the command above and restart the game. The installed `settings.json` is
also a configuration copy; editing it alone does not change the Lua data. To
use it as input:

```powershell
python addons/reassembler-expanded/build.py --config "$env:USERPROFILE/Saved Games/Reassembly/mods/reassembler-expanded/settings.json" --install
```

Use `--workshop`, `--log`, and `--destination` for another installation. Automatic
faction discovery requires Workshop Reassembler (`469212517`) in the launch log.
For a custom/local version, set `targetFaction` to the numeric loaded faction ID.
Rebuild on another computer against its own launch log; faction relocation can
differ. This package is for local installation, not direct Workshop publishing.
Local block IDs occupy 17000–25999 and shape IDs 100–9999. Other local mods must
not use the same IDs; the block collision check uses the available game log.

**Keep `registry.json`.** It preserves imported block/shape IDs across rebuilds,
including when a source is removed or reordered. Installation refuses to change
previously assigned IDs and backs up the old add-on under `generated-backups/`.
Generated third-party data is ignored by Git and intended for personal use.
Do not redistribute it as your own mod without permission from its authors.

Changing a part's width after building ships with it can break their layout.
Changing stats is the safer way to tune existing fleets. Removing a source or
this add-on while ships still use its copied blocks can leave missing blocks;
keep the installed add-on for those saves. Ordinary save backups remain useful
before experimenting with new parts. The installer never edits save slots,
Workshop files, enabled-mod indexes, regions, P caps or multiplayer code.

## Validation

```powershell
python -m unittest discover -s addons/reassembler-expanded -p "test_*.py"
```

`check_game.py` additionally loads the generated files in this computer's actual
July 2025 game build, using a fresh redirected profile, disabled Steam/network,
a hidden window, and a bounded process lifetime. It requires the bundled Python
runtime and Frida already installed for native research in `.runtime/python-tools`.
It loads no multiplayer diagnostic DLL and stops only its own process.

The verified run loaded 1,551 block definitions and all copied shapes with no
add-on parse/unknown-reference errors. A connected three-block fixture produced
10,300 power/sec, 30,900 power storage and 30,100 R capacity: the specified utility
stats plus the vanilla test command block's 300/sec, 900 power and 100 R. Native
readback confirmed both utility blocks' mass and health. The game's pre-existing
duplicate vanilla block 504 warning also occurs without this add-on.

Campaign unlock UI, sustained full-ship combat and every imported weapon/drone
remain to be checked in play. No player save was opened during these tests.

Format and palette behavior follow the developer's
[modding documentation](https://www.anisopteragames.com/docs/) and
[sandbox console documentation](https://www.anisopteragames.com/sandbox-console-docs/).
