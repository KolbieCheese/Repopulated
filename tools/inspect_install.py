"""Read-only Reassembly installation probe. Does not launch, inject into, or patch the game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct


def pe_info(exe):
    data = exe.read_bytes()
    if data[:2] != b'MZ':
        raise ValueError('Not a PE executable')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directories = optional + (112 if magic == 0x20b else 96)
    sections = []
    for i in range(count):
        start = optional + size + 40 * i
        virtual_size, va, raw_size, raw = struct.unpack_from('<IIII', data, start + 8)
        sections.append((va, max(virtual_size, raw_size), raw))

    def offset(rva):
        for va, length, raw in sections:
            if va <= rva < va + length:
                return raw + rva - va
        raise ValueError('Unmapped PE address')

    def string(rva):
        start = offset(rva)
        return data[start:data.index(b'\0', start)].decode('ascii', errors='replace')

    exports = []
    export_rva = struct.unpack_from('<I', data, directories)[0]
    if export_rva:
        start = offset(export_rva)
        names_count = struct.unpack_from('<I', data, start + 24)[0]
        names_rva = struct.unpack_from('<I', data, start + 32)[0]
        for i in range(names_count):
            exports.append(string(struct.unpack_from('<I', data, offset(names_rva) + i * 4)[0]))
    imports = []
    import_rva = struct.unpack_from('<I', data, directories + 8)[0]
    if import_rva:
        start = offset(import_rva)
        while any(data[start:start + 20]):
            name = struct.unpack_from('<I', data, start + 12)[0]
            imports.append(string(name))
            start += 20
    strings = [s.decode('ascii') for s in re.findall(rb'[ -~]{6,}', data)]
    interesting = [s for s in strings if any(k in s.lower() for k in
                   ['headless', 'multiplayer', '.pdb', 'knetwork', 'entering headless']) and len(s) < 250]
    return {'machine': hex(machine), 'sha256': hashlib.sha256(data).hexdigest(),
            'exports': exports, 'import_libraries': imports, 'selected_strings': interesting}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path)
    parser.add_argument('--save-dir', type=Path, default=Path.home() / 'Saved Games' / 'Reassembly')
    parser.add_argument('--out', type=Path, help='Write a probe report (outside game directories)')
    parser.add_argument('--mod-spec', type=Path, help='Write ordered mod spec inferred from last game log; review before use')
    parser.add_argument('--brief', action='store_true', help='Print counts instead of the full export table')
    args = parser.parse_args()
    game = args.game_dir
    if not game:
        steam = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Steam'
        roots = [steam]
        library_file = steam / 'steamapps' / 'libraryfolders.vdf'
        if library_file.exists():
            roots += [Path(p.replace('\\\\', '\\')) for p in
                      re.findall(r'"path"\s+"([^"]+)"', library_file.read_text(encoding='utf-8'))]
        for root in roots:
            candidate = root / 'steamapps' / 'common' / 'Reassembly'
            if (candidate / 'win64' / 'ReassemblyRelease.exe').exists():
                game = candidate
                break
    if not game:
        parser.error('Installation not found; pass --game-dir')
    game = game.resolve()
    exe = game / 'win64' / 'ReassemblyRelease.exe'
    report = {'game_dir': str(game), 'executable': pe_info(exe), 'adapter_available': False}
    log = args.save_dir / 'data' / 'log_latest.txt'
    spec = {'gameExe': str(exe), 'mods': []}
    if log.exists():
        text = log.read_text(encoding='utf-8', errors='replace')
        version = re.search(r'^Build Version: (.+)$', text, re.M)
        report['build_version'] = version.group(1).strip() if version else None
        for record in re.findall(r'\[MODS\] Loading \{([^\r\n]+?)\}', text):
            match = re.search(r'type=WORKSHOP, steam_pfid=0x([0-9a-fA-F]+)', record)
            if not match:
                raise ValueError('Non-Workshop mod in log; construct an explicit ordered spec manually')
            ident = str(int(match.group(1), 16))
            mod_path = game.parent.parent / 'workshop' / 'content' / '329130' / ident
            if not mod_path.is_dir():
                raise ValueError('Cannot locate active mod ' + ident)
            spec['mods'].append({'id': 'workshop:' + ident, 'path': str(mod_path)})
        report['last_log_mod_count'] = len(spec['mods'])
        report['mod_spec_warning'] = 'Order inferred from last launch log. Confirm current enabled mods and order in game before using.'
    cvars = args.save_dir / 'data' / 'cvars.txt'
    if cvars.exists():
        report['relevant_cvars'] = [line.strip() for line in cvars.read_text(encoding='utf-8').splitlines()
                                    if any(key in line for key in ['kHeadlessMode', 'kTournamentHeadless', 'kNetworkEnable'])]
    for output, content in [(args.out, report), (args.mod_spec, spec)]:
        if output:
            resolved = output.resolve()
            for protected in [game, args.save_dir.resolve()]:
                if resolved == protected or protected in resolved.parents:
                    raise ValueError('Probe output must stay outside game and save directories')
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(json.dumps(content, indent=2) + '\n', encoding='utf-8')
    if args.brief:
        report['executable']['export_count'] = len(report['executable'].pop('exports'))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
