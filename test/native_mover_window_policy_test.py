from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for native mover window policy')
class NativeMoverWindowPolicyTests(unittest.TestCase):
    def check_js(self,code):
        functions=SCRIPT[SCRIPT.index('function retainVisualFrame('):SCRIPT.index('function applyMotionFrame(')]
        fixture=r'''
const assert=require('assert'),calls=[];
const cfg={nativeCampaignClient:true,fastMotion:true},presentationDelayMs=100;
const frame=(seq,time,rows=[{root:7,id:seq}])=>({seq,sourceTimeMs:time,visualEpoch:0,
 health:{buffer:'h'+seq,count:1},blocks:{buffer:'b'+seq,count:1},projectiles:{buffer:'p'+seq,count:1},
 movers:{buffer:rows,count:rows.length}});
const a=frame(1,100),b=frame(2,200),future=frame(3,300),preparedVisualFrames=[a,b,future];
let lastMotion=a,lastVisualFrame=null,lastHealthFrame=null,lastMoverWindow=null,visualFrameApplications=0,view=150;
let visualHistoryEpoch=0,visualHistoryResets=0,windowResult=null;
const viewSourceTimeReader=()=>view,visualBegin=t=>calls.push(['source',t]);
const realtimeHealthApply=(zone,buffer)=>{calls.push(['health',buffer]);return 1;};
const realtimeVisualApply=(zone,buffer,n,projectiles)=>{calls.push(['visual',buffer,projectiles]);return 1;};
const realtimeMoversApply=(buffer,count)=>{calls.push(['legacy',buffer,count]);return count;};
let realtimeMoverWindowApply=(rowsA,countA,timeA,rowsB,countB,timeB)=>{
 assert.strictEqual(typeof countA,'number');assert.strictEqual(typeof countB,'number');
 assert.strictEqual(typeof timeA,'number');assert.strictEqual(typeof timeB,'number');
 calls.push(['window',rowsA,countA,timeA,rowsB,countB,timeB]);return windowResult===null?countA:windowResult;
};
const nativeStage=()=>{},timing=()=>{},ptr=value=>value;
__FUNCTIONS__
'''.replace('__FUNCTIONS__',functions)
        result=subprocess.run([shutil.which('node'),'-e',fixture+code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_endpoint_requires_accepted_pose_and_empty_future_is_distinct_from_missing_future(self):
        self.check_js(r'''
assert.strictEqual(nextMoverFrame(a),null); // B and future are prepared but their poses are not accepted.
consumeVisualFrame(null);
assert.deepStrictEqual(calls.at(-1),['window',a.movers.buffer,1,100,0,0,100]);
lastMotion=b;b.movers={buffer:[],count:0};calls.length=0;
assert.strictEqual(nextMoverFrame(a),b);consumeVisualFrame(null);
assert.deepStrictEqual(calls,[['window',a.movers.buffer,1,100,b.movers.buffer,0,200]]);
assert.strictEqual(lastVisualFrame,a);assert.strictEqual(lastHealthFrame,a);
assert.strictEqual(visualFrameApplications,1); // Accepting B cannot advance the discrete frame ahead of the view clock.
calls.length=0;consumeVisualFrame(null);assert.strictEqual(calls.length,0);
''')

    def test_new_endpoint_does_not_replay_weapons_health_and_geometry_restores_pair_without_advancing(self):
        self.check_js(r'''
consumeVisualFrame(null);lastMotion=b;calls.length=0;consumeVisualFrame(null);
assert.deepStrictEqual(calls,[['window',a.movers.buffer,1,100,b.movers.buffer,1,200]]);
assert.deepStrictEqual(lastMoverWindow,{a:1,b:2,epoch:0});
calls.length=0;view=-1;consumeVisualFrame(null);assert.strictEqual(calls.length,0); // Busy source getter retains A.
consumeVisualFrame(null,true);
assert.deepStrictEqual(calls,[['source',100],['health','h1'],['window',a.movers.buffer,1,100,b.movers.buffer,1,200]]);
assert.strictEqual(lastHealthFrame,a);assert.strictEqual(lastVisualFrame,a);assert.strictEqual(visualFrameApplications,1);
assert.deepStrictEqual(lastMoverWindow,{a:1,b:2,epoch:0});
windowResult=-1;assert.throws(()=>consumeVisualFrame(null,true),/mover window apply failed: -1/);
windowResult=0;assert.throws(()=>consumeVisualFrame(null,true),/mover window apply failed: 0/); // Partial native acceptance is rejected.
''')

    def test_membership_is_passed_unchanged_and_source_epochs_gaps_are_strict(self):
        self.check_js(r'''
a.movers={buffer:[{root:7,id:10},{root:7,id:20}],count:2};
b.movers={buffer:[{root:7,id:10},{root:8,id:30}],count:2};lastMotion=b;
consumeVisualFrame(null);
assert.deepStrictEqual(calls.at(-1),['window',a.movers.buffer,2,100,b.movers.buffer,2,200]);
// The native stable root/block-ID join must see unmatched A and B records; JS does not merge or invent membership.
future.visualEpoch=1;lastMotion=future;assert.strictEqual(nextMoverFrame(a),null);
lastMotion=b;b.sourceTimeMs=99;assert.throws(()=>nextMoverFrame(a),/Invalid realtime mover window source time/);
b.sourceTimeMs=601;assert.throws(()=>nextMoverFrame(a),/Invalid realtime mover window source time/);
b.sourceTimeMs=600;assert.strictEqual(nextMoverFrame(a),b);
b.sourceTimeMs=100;assert.strictEqual(nextMoverFrame(a),b);
''')

    def test_legacy_and_unavailable_window_keep_original_mover_apply(self):
        self.check_js(r'''
for(const mode of ['legacy','slow','missing']){
 cfg.nativeCampaignClient=mode!=='legacy';cfg.fastMotion=mode!=='slow';
 if(mode==='missing')realtimeMoverWindowApply=null;
 lastVisualFrame=null;calls.length=0;consumeVisualFrame(null);
 assert.deepStrictEqual(calls.at(-1),['legacy',a.movers.buffer,1]);assert.strictEqual(lastMoverWindow,null);
}
''')
        self.assertIn("if(cfg.nativeCampaignClient)realtimeMoverWindowApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyMoverWindow'),'int',['pointer','int','double','pointer','int','double']);",SCRIPT)


if __name__=='__main__':unittest.main()
