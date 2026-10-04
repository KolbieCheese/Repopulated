"""Exercise production frame deadlines and failure paths without a game."""
from pathlib import Path
import subprocess
import unittest


class NativeFramePacingTest(unittest.TestCase):
    def test_native_deadlines_and_bounded_waits(self):
        root=Path(__file__).resolve().parents[1]
        compiler=root/'.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():self.skipTest('Native Zig toolchain unavailable')
        executable=root/'.runtime/native-frame-pacing-test.exe'
        built=subprocess.run([str(compiler),'cc','-O2','-Wall','-Wextra',str(root/'test/native_frame_pacing_test.c'),'-o',str(executable)],capture_output=True,text=True)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        checked=subprocess.run([str(executable)],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        self.assertIn('timeout/failure diagnostics',checked.stdout)


if __name__=='__main__':unittest.main()
