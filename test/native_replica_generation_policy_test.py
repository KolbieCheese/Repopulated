from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for native generation policy')
class NativeReplicaGenerationPolicyTests(unittest.TestCase):
    def test_generation_removal_routes_exact_metadata_and_rejects_invalid_requests(self):
        functions=SCRIPT[SCRIPT.index('function replicaContinuity('):SCRIPT.index('function comparisonViewSettings(')]
        code=r'''
const assert=require('assert'),calls=[],zone={};
const cfg={};let sceneGateReady=false,replicaBatchRemover=null,replicaBatchMessage=null,replicaFragmentReplacementRemover=null;
const replicaRemover=(actual,id)=>{assert.strictEqual(actual,zone);calls.push(['remove',id]);return 1;};
let result=1;
const replicaReplacementRemover=(actual,id,command,faction)=>{
 assert.strictEqual(actual,zone);calls.push(['replace',id,command,faction]);return result;
};
__FUNCTIONS__
const ident=0x70000002,command=0x78000020;
applyReplicaRemovals(zone,{remove:[ident,20],continuity:[[ident,command,20008]]});
assert.deepStrictEqual(calls,[['replace',ident,command,20008],['remove',20]]);
calls.length=0;applyReplicaRemovals(zone,{remove:[ident]});assert.deepStrictEqual(calls,[['remove',ident]]);
calls.length=0;applyReplicaRemovals(zone,{remove:[20],continuity:[[20,20,0,2]]});assert.deepStrictEqual(calls,[['remove',20]]);
replicaFragmentReplacementRemover=()=>{throw Error('Neutral legacy path must reset normally');};
calls.length=0;applyReplicaRemovals(zone,{remove:[20],continuity:[[20,20,0,2]]});assert.deepStrictEqual(calls,[['remove',20]]);
for(const continuity of [null,{},[[ident,command]],[[ident,command,20008],[ident,command,20008]],
 [[ident+1,command,20008]],[[ident,0,20008]],[[ident,0x100000000,20008]],[[ident,command,-1]],
 [[ident,command,0x80000000]],[[ident,command,'20008']],[[ident,command,NaN]],
 [[ident,command,0,2]],[[ident,ident,8,2]],[[ident,ident,0,1]],[[ident,ident,0,3]],[[ident,ident,0,'2']]]){
 const before=calls.length;
 assert.throws(()=>applyReplicaRemovals(zone,{remove:[ident],continuity}),/Invalid incremental continuity/);
 assert.strictEqual(calls.length,before);
}
result=-2;assert.throws(()=>applyReplicaRemovals(zone,{remove:[ident],continuity:[[ident,command,20008]]}),/Replica removal failed: -2 id=0x70000002/);
'''.replace('__FUNCTIONS__',functions)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        self.assertIn("dll.getExportByName('RepopulatedRemoveReplicaForReplacement'),'int',['pointer','uint','uint','int']",SCRIPT)
        self.assertIn("new NativeFunction(fragmentExport,'int',['pointer','uint','uint'])",SCRIPT)


if __name__=='__main__':unittest.main()
