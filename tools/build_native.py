"""Compile the Windows x64 diagnostic with an already unpacked Zig compiler."""
import argparse
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--zig', type=Path, default=root / '.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe')
args = parser.parse_args()
if not args.zig.is_file():
    parser.error('Install an official Zig compiler and pass --zig <zig.exe>')
subprocess.run([str(args.zig.resolve()), 'cc', '-target', 'x86_64-windows-gnu', '-shared', '-O2',
                '-Wall', '-Wextra', str(root / 'native/diagnostic.c'), '-o', str(root / '.runtime/RepopulatedDiagnostic.dll')], check=True)
print(root / '.runtime/RepopulatedDiagnostic.dll')
