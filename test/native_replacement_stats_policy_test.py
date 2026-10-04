from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for replacement telemetry policy')
class NativeReplacementStatsPolicyTests(unittest.TestCase):
    def test_optional_atomic_native_counters_keep_exact_kind_labels_and_reject_invalid_values(self):
        function=SCRIPT[SCRIPT.index('function readReplacementStats('):SCRIPT.index('function configureSceneGate(')]
        code=r'''
const assert=require('assert');let replacementStatsReader=null,values=[1,2,3,4,5,6,7,8];
const replacementStatsBuffer={add:offset=>({readDouble:()=>values[offset/8]})};
__FUNCTION__
assert.deepStrictEqual(readReplacementStats(),{});
replacementStatsReader=buffer=>assert.strictEqual(buffer,replacementStatsBuffer);
assert.deepStrictEqual(readReplacementStats(),{ownedDetached:1,neutralDetached:2,ownedBound:3,neutralBound:4,
 ownedRejected:5,neutralRejected:6,unannouncedResets:7,ordinaryResets:8});
values[2]=-1;assert.throws(()=>readReplacementStats(),/Invalid native replacement stats/);
values[2]=NaN;assert.throws(()=>readReplacementStats(),/Invalid native replacement stats/);
'''.replace('__FUNCTION__',function)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)


if __name__=='__main__':unittest.main()
