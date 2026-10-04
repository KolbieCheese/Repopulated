"""Exercise the production idle transaction and timeout contract without games."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest


class NativeSceneGateTest(unittest.TestCase):
    def test_native_idle_transaction_and_fail_closed_timeout(self):
        root=Path(__file__).resolve().parents[1]
        compiler=root/'.runtime/toolchain/zig-x86_64-windows-0.15.2/zig.exe'
        if not compiler.is_file():self.skipTest('Native Zig toolchain unavailable')
        executable=root/'.runtime/native-scene-gate-test.exe'
        built=subprocess.run([str(compiler),'cc','-O2','-Wall','-Wextra',str(root/'test/native_scene_gate_test.c'),'-o',str(executable)],capture_output=True,text=True)
        self.assertEqual(built.returncode,0,built.stdout+built.stderr)
        checked=subprocess.run([str(executable)],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        self.assertIn('bounded fail-closed timeout passed',checked.stdout)
        with tempfile.TemporaryDirectory(prefix='repopulated-scene-gate-') as folder:
            log=Path(folder)/'native-log.jsonl';log.write_text('')
            environment=dict(os.environ,REPOPULATED_DIAGNOSTIC_LOG=str(log))
            failed=subprocess.run([str(executable),'--fatal'],capture_output=True,text=True,env=environment,timeout=5)
            self.assertEqual(failed.returncode,70)
            self.assertEqual(json.loads(log.read_text())['type'],'scene-gate-fatal')


if __name__=='__main__':unittest.main()
