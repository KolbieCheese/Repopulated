"""Generate a LOCAL Reassembler expansion from installed mod data (Python 3.9+).

Never executes Lua or edits Workshop files, save slots, faction/region definitions.
Generated third-party definitions are personal compatibility data, not vendored source.
"""
import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}(),=]|[^\s{}(),=]+')


def read_data(path, stack=()):
    path = path.resolve()
    if path in stack:
        raise ValueError(f'Recursive include: {path}')
    text = path.read_text(encoding='utf-8-sig')
    # Strip comments while preserving strings; process includes before tokenizing.
    text = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|--[^\n]*|//[^\n]*',
                  lambda m: '' if m[0].startswith(('--', '//')) else m[0], text)
    def include(m):
        included = (path.parent / m[1]).resolve()
        if not included.is_relative_to(path.parent):
            raise ValueError(f'Include outside source directory: {included}')
        return read_data(included, stack + (path,))
    text = re.sub(r'^\s*#include\s+"([^"]+)"\s*$', include, text, flags=re.M)
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|#[^\n]*',
                  lambda m: '' if m[0].startswith('#') else m[0], text)


def parse(text):
    tokens = TOKEN.findall(text)
    i = 0
    def table():
        nonlocal i
        if tokens[i] != '{':
            raise ValueError('Expected data table')
        i += 1
        result = []
        while i < len(tokens) and tokens[i] != '}':
            if tokens[i] == ',':
                i += 1
                continue
            key = None
            if i + 1 < len(tokens) and tokens[i + 1] == '=':
                key = tokens[i]
                i += 2
            if tokens[i] == '{':
                value = table()
            else:
                parts, parens = [], 0
                while i < len(tokens):
                    token = tokens[i]
                    if parens == 0 and token in (',', '}'):
                        break
                    if parens == 0 and parts and i + 1 < len(tokens) and tokens[i + 1] == '=':
                        break
                    if token == '(':
                        parens += 1
                    elif token == ')':
                        parens -= 1
                    parts.append(token)
                    i += 1
                value = ''.join(parts)
            result.append((key, value))
        if i >= len(tokens):
            raise ValueError('Unclosed table')
        i += 1
        return result
    result = table()
    if i != len(tokens):
        raise ValueError(f'Unexpected trailing data at token {i}: {tokens[max(0,i-8):i+12]}')
    return result


def emit(table):
    return '{' + ', '.join((k + '=' if k is not None else '') +
                          (emit(v) if isinstance(v, list) else str(v)) for k, v in table) + '}'


def get(table, key, default=None):
    return next((v for k, v in reversed(table) if k == key), default)


def put(table, key, value):
    table[:] = [(k, v) for k, v in table if k != key]
    table.append((key, str(value)))


def number(value):
    return int(str(value), 0)


def records(path):
    return [v for k, v in parse(read_data(path)) if k is None and isinstance(v, list)]


def log_mods(path):
    text = path.read_text(encoding='utf-8', errors='replace')
    result = {}
    for line in text.splitlines():
        m = re.search(r'\[MODS\] Mod \{.*?steam_pfid=(0x[\da-f]+).*?relocId (\d+) changes factions \{([^}]*)\}.*?blocks \{([^}]*)\}', line)
        if m:
            result[str(int(m[1], 16))] = {
                'reloc': int(m[2]), 'factions': [int(x.strip()) for x in m[3].split(',') if x.strip()],
                'blocks': {int(source): int(actual, 16) for actual, source in
                           re.findall(r'(0x[\da-f]+)=(\d+)', m[4])}}
    return result


def validate(config):
    for section in ('reactor', 'storage'):
        for key, value in config[section].items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{section}.{key} must be positive and finite')
        width = config[section]['width']
        if width % 10 or not 20 <= width <= 200:
            raise ValueError(f'{section}.width must be a multiple of 10 between 20 and 200')
        if not isinstance(config[section]['points'], int):
            raise ValueError('points must be an integer')


def build(config_path, workshop, log_path, output):
    config = json.loads(config_path.read_text())
    validate(config)
    mods = log_mods(log_path)
    target = config['targetFaction']
    if target == 'auto':
        if '469212517' not in mods:
            raise ValueError('Launch the game with Reassembler enabled first; no relocation found in log')
        target = next((x for x in mods['469212517']['factions'] if x > 100), None)
    if not isinstance(target, int) or target <= 100:
        raise ValueError('targetFaction must be the loaded Reassembler faction ID (>100)')
    output.mkdir(parents=True, exist_ok=True)
    registry_path = ROOT / 'registry.json' if output.resolve() == (ROOT / 'generated').resolve() else output / 'registry.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {'blocks': {}, 'shapes': {}}
    def allocate(kind, key):
        entries = registry[kind]
        if key not in entries:
            entries[key] = max(entries.values(), default=17001 if kind == 'blocks' else 101) + 1
        limit = 25999 if kind == 'blocks' else 9999
        if entries[key] > limit:
            raise ValueError(f'Too many imported {kind}')
        return entries[key]
    # Resolve custom source shapes into this add-on's own namespace.
    shape_maps, shape_defs, source_blocks, hashes = {}, [], {}, {}
    for ident in config['sourceWorkshopIds']:
        if ident not in mods:
            raise ValueError(f'Source Workshop mod {ident} was not enabled in the latest game log')
        folder = workshop / ident
        block_path = folder / 'blocks.lua'
        if not block_path.exists():
            raise ValueError(f'No blocks.lua: {folder}')
        source_blocks[ident] = records(block_path)
        hashes[ident] = hashlib.sha256(read_data(block_path).encode()).hexdigest()
        mapping = {}
        shape_path = folder / 'shapes.lua'
        if shape_path.exists():
            for shape in records(shape_path):
                old = number(shape[0][1])
                new = allocate('shapes', f'{ident}:{old}')
                mapping[old] = new
                shape[0] = (None, str(new))
                shape_defs.append(shape)
        shape_maps[ident] = mapping
    for ident in config['sourceWorkshopIds']:
        for shape in shape_defs:
            mirror = get(shape, 'mirror_of')
            # Each assigned shape ID identifies its source namespace.
            if mirror is not None and number(shape[0][1]) in shape_maps[ident].values():
                put(shape, 'mirror_of', shape_maps[ident][number(mirror)])
    clones, skipped = [], []
    for ident, blocks in source_blocks.items():
        definitions = {number(b[0][1]): b for b in blocks}
        def expand(block, stack=()):
            block = copy.deepcopy(block)
            parent = get(block, 'extends')
            if parent is not None:
                parent_id = number(parent)
                if parent_id in stack or parent_id not in definitions:
                    raise ValueError(f'Invalid inheritance {ident}:{parent_id}')
                inherited = expand(definitions[parent_id], stack + (parent_id,))
                own = {k for k, v in block if k is not None}
                block = [block[0]] + [(k, v) for k, v in inherited[1:] if k not in own] + block[1:]
                block = [(k, v) for k, v in block if k != 'extends']
            def nested(table):
                for index, (key, value) in enumerate(table):
                    if key == 'replicateBlock':
                        if isinstance(value, str) and re.fullmatch(r'\d+|0x[\da-fA-F]+', value):
                            ref = number(value)
                            if ref in stack or ref not in definitions:
                                raise ValueError(f'Invalid replicateBlock {ident}:{ref}')
                            value = expand(definitions[ref], stack + (ref,))
                            # Embedded blocks are independent descriptions, not new registered IDs.
                            value = value[1:]
                            table[index] = (key, value)
                        elif isinstance(value, list):
                            value = expand(value, stack) if value and value[0][0] is None else value
                            table[index] = (key, value)
                    if isinstance(value, list):
                        nested(value)
            nested(block)
            return block
        blocks = [expand(b, (number(b[0][1]),)) for b in blocks]
        eligible = []
        for block in blocks:
            old = number(block[0][1])
            features = str(get(block, 'features', ''))
            flags = set(re.split(r'\s*\|\s*', features))
            if (not config['includeCommandBlocks'] and 'COMMAND' in flags) or flags & {'NOPALETTE', 'ENVIRONMENT', 'SEED', 'ROOT'}:
                skipped.append(f'{ident}:{old}')
            # A log entry proves the game actually loaded this block.
            if (1 <= old < 200 or 17000 <= old < 26000) and old not in mods[ident]['blocks']:
                raise ValueError(f'Block {ident}:{old} missing from game relocation log; relaunch after mod updates')
            eligible.append((old, block))
        id_map = {old: allocate('blocks', f'{ident}:{old}') for old, _ in eligible}
        def rewrite(table, block_context=False):
            if block_context and table and table[0][0] is None and isinstance(table[0][1], str):
                old_id = number(table[0][1])
                table[0] = (None, str(id_map.get(old_id, mods[ident]['blocks'].get(old_id, old_id))))
            for index, (key, value) in enumerate(table):
                if isinstance(value, list):
                    rewrite(value, key == 'replicateBlock')
                elif key == 'shape' and re.fullmatch(r'\d+|0x[\da-fA-F]+', value):
                    old_shape = number(value)
                    if old_shape < 100:
                        continue
                    if old_shape not in shape_maps[ident]:
                        raise ValueError(f'Unknown numeric shape {ident}:{old_shape}; use an exported shape definition')
                    table[index] = (key, str(shape_maps[ident][old_shape]))
                elif key in ('group', 'faction', 'explodeFaction'):
                    old_group = number(value)
                    relocated = 100000 + mods[ident]['reloc'] * 10000 + old_group if 20 <= old_group < 100 else old_group
                    table[index] = (key, str(target if key in ('group', 'faction') else relocated))
                elif key == 'ident':
                    old_id = number(value)
                    table[index] = (key, str(id_map.get(old_id, mods[ident]['blocks'].get(old_id, old_id))))
            if get(table, 'group') is not None:
                put(table, 'group', target)
        for old, block in eligible:
            rewrite(block, True)
            put(block, 'group', target)
            features = get(block, 'features', '')
            flags = set(features.split('|')) - {'', 'PALETTE', 'NOPALETTE'}
            flags.add('NOPALETTE' if f'{ident}:{old}' in skipped else 'PALETTE')
            put(block, 'features', '|'.join(sorted(flags)))
            clones.append(block)
    def square(ident, width):
        half, ports = width / 2, []
        for edge in range(4):
            for port in range(int(width / 10)):
                ports.append('{%d, %.8f}' % (edge, (port + .5) / (width / 10)))
        return '{%d, {{verts={{%g,-%g},{-%g,-%g},{-%g,%g},{%g,%g}}, ports={%s}}}}' % (
            ident, half, half, half, half, half, half, half, half, ','.join(ports))
    reactor, storage = config['reactor'], config['storage']
    custom = [
        '{17000, name="Square Reactor XL", group=%d, shape=100, scale=1, features=PALETTE|GENERATOR, points=%d, density=%g, durability=%g, growRate=%g, generatorCapacityPerSec=%g, powerCapacity=%g, capacity=0.001, explodeDamage=%g, explodeRadius=%g, fillColor=0x173b48, fillColor1=0x247c91, lineColor=0x90eaff, blurb="Capital-ship reactor. High output; heavy, costly, and dangerous when destroyed."}' % (
            target, reactor['points'], reactor['density'], reactor['durability'], reactor['growRate'], reactor['generation'], reactor['powerStorage'], reactor['explodeDamage'], reactor['explodeRadius']),
        '{17001, name="Square Resource Vault XL", group=%d, shape=101, scale=1, features=PALETTE, points=%d, density=%g, durability=%g, growRate=%g, capacity=%g, fillColor=0x40341b, fillColor1=0x78602b, lineColor=0xffd276, blurb="Heavy capital-ship resource storage. Requires an existing tractor to collect resources."}' % (
            target, storage['points'], storage['density'], storage['durability'], storage['growRate'], storage['capacity'])]
    all_ids = {17000, 17001} | set(registry['blocks'].values())
    loaded_ids = {actual for mod in mods.values() for actual in mod['blocks'].values()}
    if all_ids & loaded_ids:
        raise ValueError('Local block IDs conflict with loaded mods; do not install this output')
    (output / 'blocks.lua').write_text('-- Generated local compatibility add-on. Keep registry.json.\n{\n' + ',\n'.join(custom + [emit(x) for x in clones]) + '\n}\n', encoding='utf-8')
    (output / 'shapes.lua').write_text('{\n' + ',\n'.join([square(100, reactor['width']), square(101, storage['width'])] + [emit(x) for x in shape_defs]) + '\n}\n', encoding='utf-8')
    registry_path.write_text(json.dumps(registry, indent=2) + '\n')
    if registry_path != output / 'registry.json':
        shutil.copyfile(registry_path, output / 'registry.json')
    report = {'targetFaction': target, 'copiedBlocks': len(clones), 'buildableBlocks': len(clones) - len(skipped), 'copiedShapes': len(shape_defs), 'skippedBlocks': len(skipped), 'sourceHashes': hashes, 'sourceWorkshopIds': config['sourceWorkshopIds'], 'config': config}
    (output / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    shutil.copyfile(config_path, output / 'settings.json')
    print(json.dumps({k: report[k] for k in ('targetFaction', 'copiedBlocks', 'copiedShapes', 'skippedBlocks')}, indent=2))
    return report


def install(output, destination):
    output, destination = output.resolve(), destination.resolve()
    if output == destination:
        raise ValueError('Build directory and installation must differ')
    files = ('blocks.lua', 'shapes.lua', 'registry.json', 'build-report.json', 'settings.json')
    if destination.exists():
        if not (destination / 'registry.json').exists():
            raise ValueError('Destination is not a managed expansion; refusing to overwrite')
        old = json.loads((destination / 'registry.json').read_text())
        new = json.loads((output / 'registry.json').read_text())
        for kind in ('blocks', 'shapes'):
            if any(new[kind].get(key) != value for key, value in old[kind].items()):
                raise ValueError('Registry would change existing IDs; restore the installed registry before rebuilding')
        backup = ROOT / 'generated-backups' / str(time.time_ns())
        shutil.copytree(destination, backup)
        print('Previous add-on backed up:', backup)
    destination.mkdir(parents=True, exist_ok=True)
    for name in files:
        shutil.copyfile(output / name, destination / name)
    shutil.copyfile(ROOT / 'README.md', destination / 'README.md')
    print('Installed:', destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--workshop', type=Path, default=Path('D:/SteamLibrary/steamapps/workshop/content/329130'))
    parser.add_argument('--log', type=Path, default=Path.home() / 'Saved Games/Reassembly/data/log_latest.txt')
    parser.add_argument('--output', type=Path, default=ROOT / 'generated')
    parser.add_argument('--install', action='store_true', help='Install/update only this local add-on; existing versions are backed up')
    parser.add_argument('--destination', type=Path, default=Path.home() / 'Saved Games/Reassembly/mods/reassembler-expanded')
    args = parser.parse_args()
    build(args.config, args.workshop, args.log, args.output)
    if args.install:
        install(args.output, args.destination)


if __name__ == '__main__':
    main()
