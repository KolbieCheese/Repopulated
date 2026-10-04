from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for native replica batch policy')
class NativeReplicaBatchPolicyTests(unittest.TestCase):
    def check_js(self,code):
        functions=SCRIPT[SCRIPT.index('function replicaContinuity('):SCRIPT.index('function comparisonViewSettings(')]
        fixture=r'''
const assert=require('assert'),calls=[],messages=[],events=[],zone={};
const cfg={nativeCampaignClient:true};let sceneGateReady=true,allocations=0,result=2;
let replicaFragmentReplacementRemover=()=>{throw Error('Batch performs the neutral replacement');};
const Memory={alloc:size=>{
 allocations++;const view=new DataView(new ArrayBuffer(size));
 const point=offset=>({add:delta=>point(offset+delta),writeU32:value=>view.setUint32(offset,value,true),
  writeS32:value=>view.setInt32(offset,value,true),readU32:()=>view.getUint32(offset,true),
  readS32:()=>view.getInt32(offset,true),readU64:()=>({toNumber:()=>Number(view.getBigUint64(offset,true))})});
 return point(0);
},allocUtf8String:value=>value};
const replicaRemover=(actual,id)=>{assert.strictEqual(actual,zone);calls.push(['remove',id]);return 1;};
const replicaReplacementRemover=(actual,id,command,faction)=>{assert.strictEqual(actual,zone);calls.push(['replace',id,command,faction]);return 1;};
let replicaBatchRemover=(actual,rows,count)=>{
 assert.strictEqual(actual,zone);assert.strictEqual(typeof count,'number');
 const descriptors=Array.from({length:count},(_,i)=>{
  const row=rows.add(i*16);return [row.readU32(),row.add(4).readU32(),row.add(8).readS32(),row.add(12).readU32()];
 });calls.push(['batch',descriptors]);events.push('batch');return result;
};
const replicaBatchMessage=()=>({readUtf8String:()=> 'seq diagnostic: invalid source owner'});
const send=value=>messages.push(value);
__FUNCTIONS__
'''.replace('__FUNCTIONS__',functions)
        checked=subprocess.run([shutil.which('node'),'-e',fixture+code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_sorted_packing_exact_continuity_empty_skip_and_missing_native_roots(self):
        self.check_js(r'''
const pilot=0x70000002,command=0x78000020;
const plan={remove:[pilot,20,10],continuity:[[pilot,command,20008]]},before=JSON.stringify(plan);
assert.strictEqual(applyReplicaRemovals(zone,plan,17),2); // Three descriptors may find only two live roots.
assert.deepStrictEqual(calls,[['batch',[[10,0,0,0],[20,0,0,0],[pilot,command,20008,1]]]]);
assert.strictEqual(JSON.stringify(plan),before);assert.strictEqual(allocations,1);
result=0;calls.length=0;assert.strictEqual(applyReplicaRemovals(zone,plan),0); // Missing roots are valid housekeeping.
calls.length=0;const previousAllocations=allocations;
assert.strictEqual(applyReplicaRemovals(zone,{remove:[]}),0);assert.strictEqual(calls.length,0);
assert.strictEqual(allocations,previousAllocations);
''')

    def test_entire_descriptor_plan_is_rejected_before_any_mutation_or_allocation(self):
        self.check_js(r'''
for(const remove of [null,{},[1,1],[0],[-1],[1.5],['1'],[NaN],[Infinity],[0x100000000],Array.from({length:4097},(_,i)=>i+1)]){
 assert.throws(()=>applyReplicaRemovals(zone,{remove}),/Invalid incremental removals/);
}
for(const continuity of [null,{},[[2,20,8]],[[1,0,8]],[[1,20,-1]],[[1,20,0x80000000]],
 [[1,20,8],[1,20,8]],[[1,20,'8']],[[1,20]],[[1,NaN,8]],
 [[1,20,0,2]],[[1,1,8,2]],[[1,1,0,0]],[[1,1,0,1]],[[1,1,0,3]],[[1,1,0,2,0]]]){
 assert.throws(()=>applyReplicaRemovals(zone,{remove:[1],continuity}),/Invalid incremental continuity/);
}
assert.strictEqual(allocations,0);assert.strictEqual(calls.length,0);
''')

    def test_initial_legacy_and_unavailable_batch_keep_individual_route_and_order(self):
        self.check_js(r'''
const plan={remove:[20,10],continuity:[[20,200,8]]};
for(const mode of ['initial','legacy','unavailable']){
 sceneGateReady=mode!=='initial';cfg.nativeCampaignClient=mode!=='legacy';
 if(mode==='unavailable')replicaBatchRemover=null;
 calls.length=0;applyReplicaRemovals(zone,plan);
 assert.deepStrictEqual(calls,[['replace',20,200,8],['remove',10]]);
}
assert.strictEqual(allocations,0);
''')

    def test_neutral_kind_is_explicit_and_missing_export_or_gate_resets_ordinarily(self):
        self.check_js(r'''
const plan={remove:[20,10,30],continuity:[[20,20,0,2],[30,300,8]]};
applyReplicaRemovals(zone,plan);
assert.deepStrictEqual(calls,[['batch',[[10,0,0,0],[20,20,0,2],[30,300,8,1]]]]);
calls.length=0;replicaFragmentReplacementRemover=null;applyReplicaRemovals(zone,plan);
assert.deepStrictEqual(calls,[['batch',[[10,0,0,0],[20,0,0,0],[30,300,8,1]]]]);
replicaFragmentReplacementRemover=()=>{throw Error('Ungated root cannot inherit a neutral curve');};
for(const mode of ['initial','legacy','unavailable']){
 sceneGateReady=mode!=='initial';cfg.nativeCampaignClient=mode!=='legacy';
 if(mode==='unavailable')replicaBatchRemover=null;
 calls.length=0;applyReplicaRemovals(zone,plan);
 assert.deepStrictEqual(calls,[['remove',20],['remove',10],['replace',30,300,8]]);
}
''')

    def test_batch_failure_retains_exact_plan_context_and_outer_gate_fails_closed(self):
        start=SCRIPT.index('    if(worldLoader && pendingWorld && consoleContext) {')
        geometry=SCRIPT[start:SCRIPT.index('      let campaignMap={};',start)]+'events.push("map");}}'
        self.check_js(r'''
cfg.persistentReplica=true;cfg.measureMotion=false;cfg.fastMotion=true;cfg.followPilot=0x70000002;
let token=1,pendingWorld=null,lastSample=0,menuSnapshotApplies=0,replicaPresentedAt=0;
const sceneIdleGateEnabled=true,consoleContext={},applyingFromMenu=false;
const trySceneUpdate=()=>{events.push('begin');return token;};
const endSceneUpdate=(actual,received,committed)=>{assert.strictEqual(received,1);events.push('end'+Number(committed));};
const worldLoader=()=>{events.push('append');return 1;},replicaUpdater=()=>{events.push('update');return 1;},replicaStats=()=>{};
const timing=()=>{},nativeStage=()=>{},packedBuffer=()=>{throw Error('Conversion entered scene gate');};
const visualApply=null,visualStats=null,motionApply=null,lastMotion=null;
const Process={getModuleByName:()=>({getExportByName:name=>name})};
const NativeFunction=function(name){assert.strictEqual(name,'RepopulatedBindReplicaPlayer');return ()=>{events.push('bind');return 1;};};
function update(zone){__GEOMETRY__
const world={seq:9,path:'private',hasAdditions:true,incremental:{remove:[20,10],continuity:[[20,200,8]],poses:'00',health:'00',retained:1,replaced:2},preparedIncremental:{poses:{buffer:'owned-poses',count:1},health:{buffer:'owned-health',count:1}}};
pendingWorld=world;result=-4;
assert.throws(()=>update(zone),/Replica batch removal failed: -4 count=2 seq=9; seq diagnostic: invalid source owner/);
assert.deepStrictEqual(events,['begin','batch','end0']);
assert.deepStrictEqual(messages,[{type:'replica-removal-failed',seq:9,result:-4,remove:[10,20],continuity:[[20,200,8]],detail:'seq diagnostic: invalid source owner'}]);
events.length=0;pendingWorld=world;result=1;update(zone);
assert.deepStrictEqual(events,['begin','batch','append','update','bind','end1','map']); // Exactly one outer gate transaction.
assert.strictEqual(events.filter(value=>value==='begin').length,1);
for(const invalidResult of [-1,3,NaN,1.5]){
 result=invalidResult;assert.throws(()=>applyReplicaRemovals(zone,world.incremental),/Replica batch removal failed/);
}
'''.replace('__GEOMETRY__',geometry))
        self.assertIn("replicaBatchRemover=new NativeFunction(dll.getExportByName('RepopulatedRemoveReplicaBatch'),'int',['pointer','pointer','int']);",SCRIPT)


if __name__=='__main__':unittest.main()
