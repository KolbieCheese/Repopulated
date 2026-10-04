# Reassembly Build Menu Filters — 1.1

A standalone Windows native extension that adds a filter toolbar to the ship constructor. Works with vanilla Reassembly and modded factions. Reassembler, Reassembler Expanded, and the multiplayer mod are not required. It filters the existing available palette; it does not unlock technology or import ships.

## Run

1. Extract the entire ZIP into a folder of your choice. This is a native extension, so putting it in the game's `mods` directory alone does not activate it.
2. Close Reassembly and keep Steam running and signed in.
3. Double-click **Reassembly Filters.exe**. Select the game executable with Browse if it was not detected automatically.
4. Click **PLAY REASSEMBLY**, open your save, and enter the ship builder as usual.

The desktop download includes its private Python/Tk/Frida runtime. Players do not need Python, PowerShell, setup commands, or internet access to run it. Keep the entire extracted folder together, including `_internal`. The Play, Interface, and Help pages provide game selection, compatibility checking, preferences, and session logs.

You may close the launcher while playing; its background worker continues until the game exits. Exit Reassembly normally to save your work. Launching through Steam alone does not load the extension. Closing the game and launching it normally removes the extension for that session.

The launcher finds Reassembly in Steam library folders automatically and remembers the game path you select. This folder contains its own data parser and requires no repository checkout. `Open Launcher.vbs` is an alternate console-free entry point. Source-checkout users without the packaged executable can install 64-bit Python 3.11 or newer, run Setup.cmd once, then double-click Open Launcher.vbs. Launch Filters.cmd remains an optional diagnostic command-line entry point.

## Updating from the first download

Extract **Reassembly-Build-Menu-Filters-1.1.zip** into a new folder and run the executable from that folder. The old extracted launcher contains the startup bug. You can copy your old `settings.json` into the new folder to retain toolbar preferences. Your game saves and enabled mod order stay in Reassembly's existing Steam/profile locations; there is no save migration or reset.

Version 1.1 fixes a Steam startup race: the game's callback thread could retrieve statistics before its statistics and achievement tables existed, producing a null-read crash at RVA `0x489f9`. The extension now waits for both tables before draining queued Steam callbacks. Steam, Workshop, and Cloud remain enabled in ordinary play. If Steam cannot initialize, the launcher reports the failure and stops before presenting an empty local save list. The launcher's running status also waits for the initialization check.

If saves appear missing, close the game, start Steam and sign in to your usual account, then use this updated launcher. Do not create replacement saves or change your mod order to recover them. Open **View session log** if startup still fails; Reassembly's own latest log is in `%USERPROFILE%\Saved Games\Reassembly\data\log_latest.txt`.

## Controls

- Click a category: All, Weapons, Hull, Armor, Thrusters, Reactors, Storage, Shields, Utility, or Command.
- Click the search field and type a part name, ID, or source label. Ctrl+F focuses it; Backspace deletes; Enter or Escape ends typing.
- Click the source label to cycle through the available source factions. Shift+F6 also cycles sources.
- F6 cycles categories. Reset restores all available parts and clears search/source selection.
- F7 or Hide shows/hides the toolbar. The collapsed Filters button reopens it. Hiding keeps the current filter active; Reset clears it. Ctrl+F also opens the toolbar.
- Click **Sort** and choose a stat from the menu. Click **High to low / Low to high** to reverse the order. F8 cycles sorting; Shift+F8 reverses it.
- The panel starts centered at the top. Drag its header to move it. Drag the bottom-right **//** grip to resize it (65–160%, constrained by screen width). Double-click the header to restore the centered position and normal size. Movement and resizing are saved between sessions.

Sorting applies to the filtered results. Choices are game order, name, mass, maximum health, cost in P, size by area, power generation per second, power storage, resource storage, thrust, shield health, weapon DPS, weapon range, and build time. Parts without an applicable stat stay at the end in both directions. Equal values use name and ID to keep the order consistent. Reset also restores the game's original order.

Stats come from the loaded game definitions and native stat functions. Weapon values describe the individual part; they do not predict the effects of neighboring boosters, ship power shortages, or combat conditions. Build time is the game's base growth time. Size uses area, rather than bounding-box width.

The toolbar uses gray panels, outlined buttons, and gold selection highlights to match the game's interface. It draws before the game's original cursor pass, preserving the native cursor above the panel. It is an overlay rather than a replacement for the game's native widgets.

The three filters combine. A hybrid block can appear in multiple categories. Source selection uses actual faction groups, plus recorded provenance for technology imported by Reassembler Expanded. Original Reassembler copies without provenance remain grouped under their owning faction. Unrecognized mod faction names display a numeric faction label.

## Configure

Use the launcher's Interface page for initial visibility, size, centered position, and sorting. Save preferences applies these changes on the next launch. Reset layout restores the centered default. In-game drag/resize changes are saved immediately when you release the mouse.

Advanced users can edit `settings.json` before launching. `toolbarX: null` means centered; a number sets the horizontal position. `toolbarY` sets the vertical position. `toolbarScale` controls size (1 = 100%). Set `toolbarVisible` to false to start collapsed. `defaultSort` chooses the initial sort (for example `mass`, `health`, `generation`, or `Default`); `sortDirection` is `asc` or `desc`. Other sort keys are `name`, `cost`, `area`, `powerStorage`, `resources`, `thrust`, `shieldHealth`, `dps`, `range`, and `buildTime`.

`armorDurabilityThreshold` defaults to 8. Armor falls back to the block name or durability; storage falls back to names such as Storage, Container, Cargo, and Vault. These categories are imperfect for unusually named mod parts. Override individual block IDs with, for example:

```json
"categoryOverrides": {
  "17000": ["Reactors"],
  "17001": ["Storage"]
}
```

Other categories use the game's feature flags. Filters last for the current game session.

## Compatibility and validation

This first version supports the installed July 2025 Windows x64 executable, SHA256 `8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c`. Both the executable hash and native instruction signatures are checked before hooking. Updated executables require new validation.

The extension uses process-local Frida hooks and an OpenGL toolbar. It does not patch the executable on disk. It maintains a separate displayed-ID vector and verifies that the original available-ID list stays unchanged. Filtering pauses while dragging. Edit Palette receives the original list; returning to construction reapplies the filter. A detected runtime error disables filtering for that process and attempts to restore the original palette.

Automated live tests use fresh redirected profiles and bounded lifetimes. They cover stock Terran and expanded Reassembler palettes, all numeric sorts in both directions, missing stats last, filtered sorting, keyboard and mouse filtering/sorting, source selection, empty results, reset, showing/hiding, and entering/leaving Edit Palette. The actual displayed ID vector is read back to verify ordering. Custom reactor/vault stats are checked against their exact expected values. Native cursor layering, dragging, resizing, scaled clicks, centered reset, and layout persistence were also checked. The extracted desktop executable and its Play-button workflow passed using the bundled runtime without an external Python installation or console window.

Steam-enabled regression tests explicitly request statistics early and slow the native loader. This reproduces the original null read without the guard and succeeds with it. These tests disable Cloud writes with `kSteamCloudEnable=0` in their private profile only. A copied existing campaign was loaded through the native save-slot path with its original mod index, and its Mother-Ship builder opened with 1,313 parts. Original Steam save and mod-index files passed before/after SHA256 checks. Screenshots were checked visually. Multiplayer coexistence has not yet been tested; implementation files are separate and findings have been shared with that agent.

Run `.venv\Scripts\python.exe launch.py --test --no-provenance` for a vanilla isolated test, or add `--addon-test` without `--no-provenance` to test the optional installed expanded faction. The optional integration reads its existing build report and provenance registry; it does not import its Python code or require it for vanilla use. Classification tests in the source checkout run with `node --test model.test.js`.

Add `--steam-test` to `--test` for the real Steam startup regression and full palette checks in a fresh profile with Cloud disabled. The packaged GUI supports `Reassembly Filters.exe --self-test report.json --steam-test` for the corresponding Play-button regression.

## Package contents

`README.md` and `README.txt` contain these instructions. The ZIP includes the graphical launcher and bundled runtime, optional source setup files, settings, native signatures, filter code, standalone Lua data reader, isolated-test helper, validation results, and third-party runtime licenses. It contains no Reassembly game executable, third-party mod definitions, save files, multiplayer files, or Reassembler expansion code. To remove it, close the game and delete the extracted folder.

Developers can rebuild the desktop runtime and ZIP with `python build_desktop.py` after installing PyInstaller 6. The packaging script can update the ZIP without rebuilding the desktop runtime. Research utilities and generated test profiles are excluded from the download.

Native entry points and offsets are recorded in `signatures.json`, `startup.js`, `runtime.js`, and the read-only research utilities. Generated test profiles and disassembly stay under ignored `research/`.
