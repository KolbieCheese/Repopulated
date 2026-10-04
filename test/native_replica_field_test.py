"""Exercise the production private Console field boundary without a game."""
from pathlib import Path
import subprocess
import unittest


class NativeReplicaFieldTest(unittest.TestCase):
    def test_private_field_preserves_live_campaign_streamer_and_native_field_lifetime(self):
        root=Path(__file__).resolve().parents[1]
        compiler=root/'.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():self.skipTest('Native Zig toolchain unavailable')
        executable=root/'.runtime/native-replica-field-test.exe'
        built=subprocess.run([str(compiler),'cc','-O2','-Wall','-Wextra',str(root/'test/native_replica_field_test.c'),'-o',str(executable)],capture_output=True,text=True)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        checked=subprocess.run([str(executable)],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        self.assertIn('Private field isolation',checked.stdout)


if __name__=='__main__':unittest.main()
