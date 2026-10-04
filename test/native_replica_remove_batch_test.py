"""Execute the production bounded batch/lifetime policy without a game."""
from pathlib import Path
import subprocess
import unittest


class NativeReplicaRemoveBatchTest(unittest.TestCase):
    def test_native_removal_batch_policy(self):
        root = Path(__file__).resolve().parents[1]
        compiler = root / '.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():
            self.skipTest('Native Zig toolchain unavailable')
        executable = root / '.runtime/native-replica-remove-batch-test.exe'
        built = subprocess.run([str(compiler), 'cc', '-O2', '-Wall', '-Wextra',
                                str(root / 'test/native_replica_remove_batch_test.c'),
                                '-o', str(executable)], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        checked = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('pointer-reuse rejection passed', checked.stdout)


if __name__ == '__main__':
    unittest.main()
