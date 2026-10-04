"""Exercise production fair idle handoff without launching Reassembly."""
from pathlib import Path
import subprocess
import unittest


class NativeSceneHandoffTest(unittest.TestCase):
    def test_native_reserved_slot_safety(self):
        root = Path(__file__).resolve().parents[1]
        compiler = root / '.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():
            self.skipTest('Native Zig toolchain unavailable')
        executable = root / '.runtime/native-scene-handoff-test.exe'
        built = subprocess.run([str(compiler), 'cc', '-O2', '-Wall', '-Wextra',
                                str(root / 'test/native_scene_handoff_test.c'),
                                '-o', str(executable)], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        checked = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('100ms committed-writer safety passed', checked.stdout)


if __name__ == '__main__':
    unittest.main()
