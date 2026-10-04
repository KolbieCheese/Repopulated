from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for scene gate policy')
class NativeSceneGatePolicyTests(unittest.TestCase):
    def check_js(self,code):
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_optional_configuration_requires_verified_thread_and_motion_deferral_preserves_queue(self):
        helpers=SCRIPT[SCRIPT.index('function sceneGateSettings('):SCRIPT.index('function installReplicaField(')]
        motion=SCRIPT[SCRIPT.index('function consumeMotion('):SCRIPT.index('function beforeZoneUpdate(')]
        code=r'''
const assert=require('assert'),events=[],zone={equals:other=>other===zone};
let sceneIdleGateEnabled=false,sceneGateReady=false,sceneGateTryBegin=null,sceneGateEnd=null,
 sceneGateStatsReader=null,sceneGateStatsBuffer=null,replicaInitialized=false,campaignReplicaZone=zone,nativeUpdateThread=7,nativeDrawThread=null;
let thread=7,acquired=1,configured=1,endResult=1;
const renderPrefix=[0x48,0x8b,0xc4,0x55,0x53,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57];
let badSignature=false;
const boundary={readByteArray:size=>{assert.strictEqual(size,15);return badSignature?new Uint8Array(15):new Uint8Array(renderPrefix);}};
const game={base:{add:offset=>{assert.strictEqual(offset,0x118730);return boundary;}}};
const Interceptor={replaceFast:(target,replacement)=>{assert.strictEqual(target,boundary);assert.strictEqual(replacement,'RepopulatedRenderBoundary');events.push('render-hook');return 'original-render';},
 revert:target=>{assert.strictEqual(target,boundary);events.push('render-revert');}};
const Process={getCurrentThreadId:()=>thread,getModuleByName:()=>({getExportByName:name=>name})};
const Memory={alloc:size=>({add:offset=>({readDouble:()=>offset/8}),size})};
const send=record=>events.push(record.action);
const NativeFunction=function(name,result,args){
 if(name==='RepopulatedConfigureSceneGate')return actual=>{assert.strictEqual(actual,zone);events.push('configure');return configured;};
 if(name==='RepopulatedTryBeginSceneUpdate')return actual=>{assert.strictEqual(actual,zone);events.push('begin');return acquired;};
 if(name==='RepopulatedEndSceneUpdate'){
  assert.deepStrictEqual(args,['pointer','int']);
  return (actual,committed)=>{assert.strictEqual(actual,zone);assert.strictEqual(typeof committed,'number');events.push('end'+committed);return endResult;};
 }
 if(name==='RepopulatedSceneGateStats')return ()=>{};
 if(name==='RepopulatedSetRenderBoundaryOriginal')return original=>{assert.strictEqual(original,'original-render');events.push('render-original');};
 throw Error(name);
};
__HELPERS__
assert.strictEqual(sceneGateSettings({}),false);assert.strictEqual(sceneGateSettings({testSceneIdleGate:false}),false);
assert.strictEqual(sceneGateSettings({sceneIdleGate:false,testSceneIdleGate:false}),false);
const valid={sceneIdleGate:true,nativeCampaignClient:true,persistentReplica:true,fastMotion:true,frameLimit:60};
assert.strictEqual(sceneGateSettings(valid),true);
assert.strictEqual(sceneGateSettings({...valid,sceneIdleGate:false,testSceneIdleGate:true}),true); // Retained fixture alias.
assert.strictEqual(sceneGateSettings({...valid,testSceneIdleGate:false}),true); // Either explicit true enables the gate.
for(const key of ['sceneIdleGate','testSceneIdleGate'])for(const invalid of [0,1,null,'true']){
 assert.throws(()=>sceneGateSettings({...valid,[key]:invalid}),/settings must be boolean/);
 assert.throws(()=>sceneGateSettings({sceneIdleGate:false,testSceneIdleGate:false,[key]:invalid}),/settings must be boolean/);
}
for(const key of ['nativeCampaignClient','persistentReplica','fastMotion','frameLimit'])assert.throws(()=>sceneGateSettings({...valid,[key]:false}),/requires the native campaign client/);
assert.throws(()=>sceneGateSettings({...valid,frameLimit:120}),/requires the native campaign client/);
configureSceneGate(zone);assert.strictEqual(events.length,0);assert.strictEqual(trySceneUpdate(zone),2);
endSceneUpdate(zone,2,true);assert.strictEqual(events.length,0);
sceneIdleGateEnabled=true;
assert.throws(()=>configureSceneGate(zone),/verified replica update thread/);assert.strictEqual(events.length,0);
replicaInitialized=true;thread=8;assert.throws(()=>configureSceneGate(zone),/verified replica update thread/);
thread=7;configureSceneGate(zone);assert.strictEqual(events.length,0);assert.strictEqual(sceneGateReady,false);
nativeDrawThread=7;assert.throws(()=>configureSceneGate(zone),/requires distinct draw and update threads/);assert.strictEqual(events.length,0);
nativeDrawThread=9;badSignature=true;assert.throws(()=>configureSceneGate(zone),/render boundary signature differs/);assert.strictEqual(events.length,0);
badSignature=false;configured=0;assert.throws(()=>configureSceneGate(zone),/configuration failed: 0/);assert.strictEqual(sceneGateReady,false);
assert.deepStrictEqual(events,['render-hook','render-original','configure','render-revert']);
configured=1;configureSceneGate(zone);assert.strictEqual(sceneGateReady,true);
const once=events.length;configureSceneGate(zone);assert.strictEqual(events.length,once);
thread=8;assert.throws(()=>trySceneUpdate(zone),/verified replica update thread/);thread=7;
acquired=-1;assert.throws(()=>trySceneUpdate(zone),/acquisition failed: -1/);
let pendingMotion={seq:2},lastMotion={seq:1},motionApply=true,streamStats={zoneSkips:0,maxApplyMs:0},applyFails=false,visualFails=false;
let pendingMotionSince=0,pendingMotionQueuedSince=0;
const motionAdmissionStats={longestPendingAgeMs:0,bootstrapPendingAgeMs:0,longestActivePendingAgeMs:0,acceptedFrames:0,deferredAttempts:0};
let candidate={seq:2},lastVisualFrame={seq:2};
const currentVisualFrame=()=>candidate;
const frame=pendingMotion;
const applyMotionFrame=(actual,input)=>{assert.strictEqual(input,frame);events.push('motion');if(applyFails)throw Error('pose failed');};
const consumeVisualFrame=()=>{events.push('visual');if(visualFails)throw Error('visual failed');};
const timing=()=>{};
__MOTION__
events.length=0;acquired=0;consumeMotion(zone);
assert.strictEqual(pendingMotion,frame);assert.strictEqual(lastMotion.seq,1);assert.deepStrictEqual(events,['begin']);
events.length=0;acquired=1;consumeMotion(zone);
assert.strictEqual(pendingMotion,null);assert.strictEqual(lastMotion,frame);assert.deepStrictEqual(events,['begin','motion','visual','end1']);
events.length=0;consumeMotion(zone);assert.deepStrictEqual(events,[]); // Same displayed frame causes no gate transaction.
candidate=null;consumeMotion(zone);assert.deepStrictEqual(events,[]); // Unready visual clock also performs no mutation.
candidate={seq:3};acquired=0;consumeMotion(zone);assert.deepStrictEqual(events,['begin']);assert.strictEqual(lastVisualFrame.seq,2);
events.length=0;pendingMotion=frame;applyFails=true;
acquired=1;
assert.throws(()=>consumeMotion(zone),/pose failed/);assert.deepStrictEqual(events,['begin','motion','end0']);
events.length=0;applyFails=false;visualFails=true;pendingMotion=frame;
assert.throws(()=>consumeMotion(zone),/visual failed/);assert.deepStrictEqual(events,['begin','motion','visual','end0']);
visualFails=false;endResult=-1;assert.throws(()=>consumeMotion(zone),/release failed: -1/);
'''
        self.check_js(code.replace('__HELPERS__',helpers).replace('__MOTION__',motion))

    def test_geometry_transaction_covers_restoration_and_bind_but_releases_before_map(self):
        start=SCRIPT.index('    if(worldLoader && pendingWorld && consoleContext) {')
        geometry=SCRIPT[start:SCRIPT.index('      let campaignMap={};',start)]+'events.push("map");}}'
        code=r'''
const assert=require('assert'),events=[],zone={};
let token=1,pendingWorld=null,appendFails=false,worldResult=1,menuSnapshotApplies=0,duringUpdate=null;
let sceneIdleGateEnabled=true,sceneGateReady=true,lastSample=123;
let pendingMotionQueuedSince=0;
const noteMotionDeferred=()=>{};
const cfg={persistentReplica:true,measureMotion:false,fastMotion:true,nativeCampaignClient:true,followPilot:0x70000002};
const trySceneUpdate=()=>{events.push('begin');return token;};
const endSceneUpdate=(actual,received,committed)=>{assert.strictEqual(actual,zone);assert.strictEqual(received,token);if(received===1)events.push('end'+Number(committed));};
const consoleContext={},applyingFromMenu=false,displayFrames=10;
const worldLoader=()=>{events.push('append');if(appendFails)throw Error('append failed');return worldResult;};
const applyReplicaRemovals=()=>events.push('remove'),replicaUpdater=(actual,poses,poseCount,health,healthCount)=>{
 assert.strictEqual(actual,zone);assert.strictEqual(poses,'owned-poses');assert.strictEqual(health,'owned-health');
 assert.strictEqual(poseCount,1);assert.strictEqual(healthCount,1);
 events.push('update');if(duringUpdate)pendingMotion=duringUpdate;return 1;
};
const replicaStats=()=>{},timing=()=>{},nativeStage=()=>{},packedBuffer=()=>{throw Error('Conversion entered scene gate');};
const Memory={allocUtf8String:value=>value,alloc:()=>({readU64:()=>({toNumber:()=>1}),add:()=>({readU64:()=>({toNumber:()=>1})})})};
let visualApply=null,visualStats=null,motionApply=true,lastMotion={seq:1},pendingMotion=null,replicaPresentedAt=0,poseFails=false;
const applyMotionFrame=(actual,frame,replay)=>{
 assert.strictEqual(actual,zone);assert.strictEqual(typeof replay,'boolean');
 events.push(replay?'motionaccept'+frame.seq:'motionrestore');
 if(poseFails)throw Error('motion failed');
};
const consumeVisualFrame=(actual,restore)=>{assert.strictEqual(actual,zone);assert.strictEqual(restore,true);events.push('visualrestore');};
const Process={getModuleByName:()=>({getExportByName:name=>name})};
const NativeFunction=function(name){assert.strictEqual(name,'RepopulatedBindReplicaPlayer');return ()=>{events.push('bind');return 1;};};
function update(zone){__GEOMETRY__
const frame={seq:2,path:'private',hasAdditions:true,incremental:{poses:'00',health:'00',retained:2,replaced:1},preparedIncremental:{poses:{buffer:'owned-poses',count:1},health:{buffer:'owned-health',count:1}}};
pendingWorld=frame;token=0;update(zone);
assert.strictEqual(pendingWorld,frame);assert.deepStrictEqual(events,['begin']);
assert.strictEqual(lastSample,0); // Only the enabled private gate retries geometry on the next native update.
events.length=0;token=1;update(zone);assert.strictEqual(pendingWorld,null);
assert.deepStrictEqual(events,['begin','remove','append','update','motionrestore','visualrestore','bind','end1','map']);
events.length=0;pendingWorld=frame;appendFails=true;assert.throws(()=>update(zone),/append failed/);
assert.deepStrictEqual(events,['begin','remove','append','end0']);
appendFails=false;pendingWorld=frame;events.length=0;worldResult=-1;
assert.throws(()=>update(zone),/gated world loading failed: -1/);assert.deepStrictEqual(events,['begin','remove','append','motionrestore','visualrestore','end0']);
worldResult=1;pendingWorld=frame;events.length=0;token=2;lastSample=123;sceneIdleGateEnabled=false;sceneGateReady=false;update(zone);
assert(!events.some(value=>value.startsWith('end'))); // Initial import never releases an unacquired gate.
assert.strictEqual(lastSample,123);
// A missed entry slot leaves its newest prepared motion available to a geometry exit slot.
sceneIdleGateEnabled=true;sceneGateReady=true;token=0;pendingWorld=frame;
const queued={seq:5};pendingMotion=queued;events.length=0;update(zone);
assert.strictEqual(pendingWorld,frame);assert.strictEqual(pendingMotion,queued);assert.strictEqual(lastMotion.seq,1);
assert.deepStrictEqual(events,['begin']);
token=1;events.length=0;update(zone);
assert.strictEqual(pendingMotion,null);assert.strictEqual(lastMotion,queued);
assert.deepStrictEqual(events,['begin','remove','append','update','motionaccept5','visualrestore','bind','end1','map']);
assert.strictEqual(events.filter(value=>value==='begin').length,1); // No nested gate or stale duplicate replay.
// An initial/unconfigured import keeps its motion queue for the later verified update thread.
token=2;pendingWorld=frame;pendingMotion={seq:6};events.length=0;update(zone);
assert.strictEqual(pendingMotion.seq,6);assert.strictEqual(lastMotion,queued);
assert.deepStrictEqual(events,['begin','remove','append','update','motionrestore','visualrestore','bind','map']);
// Native rejection remains fatal and closes the acquired transaction without advancing lastMotion.
token=1;pendingWorld=frame;poseFails=true;events.length=0;
assert.throws(()=>update(zone),/motion failed/);assert.strictEqual(lastMotion,queued);
assert.deepStrictEqual(events,['begin','remove','append','update','motionaccept6','end0']);
// A newer RPC-prepared frame during structural mutation wins; accepting a saved early queue item would add lag.
poseFails=false;pendingWorld=frame;pendingMotion={seq:8};duringUpdate={seq:9};events.length=0;update(zone);
assert.strictEqual(lastMotion,duringUpdate);assert.strictEqual(pendingMotion,null);
assert.deepStrictEqual(events,['begin','remove','append','update','motionaccept9','visualrestore','bind','end1','map']);
'''.replace('__GEOMETRY__',geometry)
        self.check_js(code)
        self.assertIn("'void',[],{scheduling:'cooperative'}",SCRIPT)
        self.assertIn('if(clusters>=0 && verifiedRoots===world.roots)configureSceneGate(zone);',SCRIPT)

    def test_initialized_private_campaign_retries_motion_at_exit_before_geometry(self):
        motion=SCRIPT[SCRIPT.index('function consumeMotion('):SCRIPT.index('function beforeZoneUpdate(')]
        update=SCRIPT[SCRIPT.index('function afterZoneUpdate('):SCRIPT.index("Interceptor.attach(game.getExportByName('?Update@GameZone",SCRIPT.index('function afterZoneUpdate('))]
        code=r'''
const assert=require('assert'),events=[],zone={equals:other=>other===zone},other={equals:()=>false};
const cfg={nativeCampaignClient:true,streamMotion:false,pilotInput:false};
let sceneGateReady=true,replicaInitialized=true,campaignReplicaZone=zone,motionApply=true;
let pendingMotion={seq:6},lastMotion={seq:5},lastVisualFrame={seq:5},acquired=0;
let pendingMotionQueuedSince=0;
const noteMotionDeferred=()=>{};
const queued=pendingMotion,streamStats={zoneSkips:0,maxApplyMs:0};
const trySceneUpdate=()=>{events.push('begin');return acquired;};
const endSceneUpdate=(actual,token,committed)=>{assert.strictEqual(actual,zone);assert.strictEqual(token,1);events.push('end'+Number(committed));};
const currentVisualFrame=()=>lastMotion;
const applyMotionFrame=(actual,frame,replay=true)=>{assert.strictEqual(actual,zone);assert.strictEqual(replay,true);events.push('accept'+frame.seq);};
const consumeVisualFrame=()=>{events.push('visual');lastVisualFrame=lastMotion;};
const timing=()=>{},reportHostAIStats=()=>{},reportHostOwnershipStats=()=>{};
let campaignAuthorityEnded=false,campaignAuthorityZone=null,pendingCheckpoint=null;
const sample=actual=>{assert.strictEqual(actual,zone);events.push('sample');};
__MOTION__
__UPDATE__
consumeMotion(zone);assert.strictEqual(pendingMotion,queued);assert.deepStrictEqual(events,['begin']);
events.length=0;acquired=1;afterZoneUpdate(zone);
assert.deepStrictEqual(events,['begin','accept6','visual','end1','sample']);
assert.strictEqual(lastMotion,queued);assert.strictEqual(pendingMotion,null);
// Queued state survives a deferred exit as well; sample/geometry may subsequently acquire its own slot.
pendingMotion={seq:7};acquired=0;events.length=0;afterZoneUpdate(zone);
assert.strictEqual(pendingMotion.seq,7);assert.deepStrictEqual(events,['begin','sample']);
// Ordinary clients/hosts and uninitialized replicas retain their prior scheduling.
for(const disable of ['gate','campaign','initialized','zone']){
 sceneGateReady=disable!=='gate';cfg.nativeCampaignClient=disable!=='campaign';replicaInitialized=disable!=='initialized';
 campaignReplicaZone=disable==='zone'?other:zone;events.length=0;afterZoneUpdate(zone);
 assert.deepStrictEqual(events,['sample']);assert.strictEqual(pendingMotion.seq,7);
}
'''.replace('__MOTION__',motion).replace('__UPDATE__',update)
        self.check_js(code)


if __name__=='__main__':unittest.main()
