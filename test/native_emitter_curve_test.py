"""Exercise actual local-exhaust curve and immutable publication without a game."""
from pathlib import Path
import subprocess
import unittest


class NativeEmitterCurveTest(unittest.TestCase):
    def test_emission_curve_and_publication(self):
        root = Path(__file__).resolve().parents[1]
        compiler = root / '.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():
            self.skipTest('Native Zig toolchain unavailable')
        executable = root / '.runtime/native-emitter-curve-test.exe'
        built = subprocess.run([str(compiler), 'cc', '-O2', '-Wall', '-Wextra',
                                str(root / 'test/native_emitter_curve_test.c'), '-o', str(executable)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        checked = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('bounded publication passed', checked.stdout)


if __name__ == '__main__':
    unittest.main()
