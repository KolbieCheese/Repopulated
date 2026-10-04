"""Verify a native sector checkpoint across two isolated game processes.

This is a build-specific research fixture, not a campaign save adapter.
It keeps all checkpoints and diagnostic logs under .runtime.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def vector(data, name, default=None):
    match = re.search(name.encode() + rb'=\{([-\d.eE+]+),\s*([-\d.eE+]+)\}', data)
    if not match:
        if default is not None:
            return default
        raise ValueError('Missing checkpoint field: ' + name)
    return [float(value) for value in match.groups()]


def inspect_checkpoint(path):
    # This narrow reader only inspects ASCII metadata in the game's mixed
    # text/binary format. The game itself reconstructs the blocks.
    data = gzip.decompress(path.read_bytes())
    offset = vector(data, 'offset')
    ships = []
    for chunk in data.split(b'cluster{')[1:]:
        position = vector(chunk, 'position')
        faction = re.search(rb'faction=(\d+)', chunk)
        if not faction or b'blocks={' not in chunk:
            raise ValueError('Missing faction or block payload')
        ships.append({'faction': int(faction[1]),
                      'position': [position[i] + offset[i] for i in range(2)],
                      'velocity': vector(chunk, 'velocity', [0.0, 0.0])})
    return ships


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    args = parser.parse_args()
    exe = args.exe.resolve()
    root = Path(__file__).resolve().parents[1]
    folder = root / '.runtime' / ('restore-check-' + str(time.time_ns()))
    folder.mkdir(parents=True)
    ship = (exe.parent.parent / 'data' / 'ships' / '8_interceptor.lua').as_posix()
    if any(c in ship for c in ';\r\n'):
        raise ValueError('Unsupported command separator in game path')
    script = folder / 'save-script.txt'
    script.write_text(f'cursor 100 100 0; import {ship}; cursor 500 100 0; '
                      f'import {ship}; sleep 1; level_save checkpoint.lua; '
                      'sleep 1; echo finished')
    probe = [sys.executable, str(root / 'tools' / 'run_native_probe.py'),
             '--exe', str(exe), '--seconds', '5']
    save_report = folder / 'save-run.json'
    subprocess.run(probe + ['--headless', '--sandbox-file', str(script),
                           '--test-control', '--report-file', str(save_report)], check=True)
    save = json.loads(save_report.read_text())
    checkpoint = Path(save['artifactDirectory']) / 'Reassembly' / 'checkpoint.lua.gz'
    expected = inspect_checkpoint(checkpoint)
    if len(expected) != 2 or not any(any(v != 0 for v in s['velocity']) for s in expected):
        raise ValueError('Expected two saved ships including a nonzero velocity')
    restore_report = folder / 'restore-run.json'
    subprocess.run(probe + ['--restore-level', str(checkpoint),
                           '--report-file', str(restore_report)], check=True)
    restore = json.loads(restore_report.read_text())
    if restore['movingEntitiesObserved'] < 1:
        raise ValueError('Restored world did not demonstrate continuing motion')
    records = [json.loads(line) for line in
               (Path(restore['artifactDirectory']) / 'native-telemetry.jsonl').read_text().splitlines()]
    first = next(record for record in records if record.get('type') == 'zone' and record.get('ships'))
    actual = sorted(first['ships'], key=lambda s: s['x'])
    expected.sort(key=lambda s: s['position'][0])
    if first['clusters'] != 2 or len(actual) != 2:
        raise ValueError('Restored ship count differs')
    for saved, loaded in zip(expected, actual):
        if saved['faction'] != loaded['faction'] or not loaded['hasCommand']:
            raise ValueError('Restored faction or command presence differs')
        for axis, key in enumerate(['x', 'y']):
            if abs(saved['position'][axis] - loaded[key]) > 0.1:
                raise ValueError('Restored position differs beyond serialization tolerance')
        for axis, key in enumerate(['vx', 'vy']):
            if abs(saved['velocity'][axis] - loaded[key]) > 0.1:
                raise ValueError('Restored velocity differs')
    result = {'schema': 1, 'sectorRestoreValidated': True,
              'campaignRestoreValidated': False, 'multiplayerValidated': False,
              'shipsVerified': 2, 'checkpointSha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              'expected': expected, 'observed': actual, 'saveRun': save, 'restoreRun': restore}
    (folder / 'result.json').write_text(json.dumps(result, indent=2))
    print('Native sector restore verified. Evidence:', folder / 'result.json')


if __name__ == '__main__':
    main()
