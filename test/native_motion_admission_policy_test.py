from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for native admission policy')
class NativeMotionAdmissionPolicyTests(unittest.TestCase):
    def run_js(self,body):
        helpers=SCRIPT[SCRIPT.index('function bindSceneHandoff('):SCRIPT.index('function installReplicaField(')]
        motion=SCRIPT[SCRIPT.index('function consumeMotion('):SCRIPT.index('function beforeZoneUpdate(')]
        setup=r'''
const assert=require('assert'),events=[],zone={equals:other=>other===zone};
let now=1000,thread=7,exportsAvailable=true,nativeResult=1,acquired=0,applyFails=false,duringApply=null;
Date.now=()=>now;
let sceneGateReady=true,campaignReplicaZone=zone,nativeUpdateThread=7;
let sceneHandoffBound=false,sceneHandoffRequest=null,sceneHandoffStatsReader=null,sceneHandoffStatsBuffer=null,sceneHandoffRequested=false;
let pendingMotion=null,lastMotion={seq:1},lastVisualFrame={seq:1},motionApply=true,replicaInitialized=true;
let pendingMotionSince=0,pendingMotionQueuedSince=0,lastAcceptedMotionAt=0;
const motionAdmissionStats={preparedFrames:0,acceptedFrames:0,supersededFrames:0,deferredAttempts:0,
 longestPendingAgeMs:0,bootstrapPendingAgeMs:0,longestActivePendingAgeMs:0,longestAcceptanceGapMs:0,
 lastPreparedSeq:0,lastAppliedSeq:0,lastPreparedAtMs:0,lastAppliedAtMs:0};
const cfg={measureMotion:true},streamStats={zoneSkips:0,maxApplyMs:0};
const Process={getCurrentThreadId:()=>thread,getModuleByName:()=>({findExportByName:name=>exportsAvailable?name:null})};
const stats=[2,2,1,1,2,1];
const Memory={alloc:()=>({add:offset=>({readDouble:()=>stats[offset/8]})})};
const NativeFunction=function(name,result,args){
 if(name==='RepopulatedRequestSceneHandoff'){
  assert.strictEqual(result,'int');assert.deepStrictEqual(args,['pointer','int']);
  return (actual,pending)=>{assert.strictEqual(actual,zone);assert.strictEqual(typeof pending,'number');events.push(['request',pending]);return nativeResult;};
 }
 assert.strictEqual(name,'RepopulatedSceneHandoffStats');return ()=>events.push(['stats']);
};
const send=record=>events.push(['diagnostic',record]);
const readSceneGateStats=()=>({state:acquired?3:1});
const trySceneUpdate=()=>{events.push(['begin']);return acquired;};
const endSceneUpdate=(actual,token,committed)=>{assert.strictEqual(actual,zone);if(token===1)events.push(['end',Number(committed)]);};
const currentVisualFrame=()=>lastVisualFrame,timing=()=>{};
const consumeVisualFrame=()=>events.push(['visual']);
const applyMotionFrame=(actual,frame)=>{
 events.push(['pose',frame.seq]);
 if(applyFails)throw Error('pose failed');
 if(duringApply){const newer=duringApply;duringApply=null;queueMotionFrame(newer);}
 noteMotionAcceptance(actual,frame);
};
const frame=(seq,received=now)=>({seq,received,sourceTimeMs:10000+seq});
__HELPERS__
__MOTION__
'''
        result=subprocess.run([shutil.which('node'),'-e',setup.replace('__HELPERS__',helpers).replace('__MOTION__',motion)+body],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_oldest_pending_survives_superseded_frames_and_requests_only_from_owner_after_threshold(self):
        self.run_js(r'''
queueMotionFrame(frame(2));assert.strictEqual(pendingMotionSince,1000);
now=1040;queueMotionFrame(frame(3));assert.strictEqual(pendingMotionSince,1000);
consumeMotion(zone,'entry');assert(!events.some(row=>row[0]==='request'));
now=1075;thread=8;assert.throws(()=>noteMotionDeferred(zone,'ui'),/verified replica update thread/);
assert(!events.some(row=>row[0]==='request'));
thread=7;consumeMotion(zone,'exit');
assert.deepStrictEqual(events.filter(row=>row[0]==='request'),[['request',1]]);
const request=events.find(row=>row[0]==='diagnostic')[1];
assert.strictEqual(request.oldestPendingAgeMs,75);assert.strictEqual(request.seq,3);
assert.strictEqual(request.preparedAtMs,1040);assert.strictEqual(request.receivedAtMs,1040);
assert.strictEqual(request.opportunity,'exit');assert.strictEqual(request.thread,7);
assert.strictEqual(request.phase,'bootstrap');
now=1100;queueMotionFrame(frame(4));consumeMotion(zone,'ui');
assert.strictEqual(pendingMotionSince,1000);assert.strictEqual(pendingMotion.seq,4);
assert.strictEqual(events.filter(row=>row[0]==='request').length,1);
assert.strictEqual(motionAdmission().longestPendingAgeMs,100);
acquired=1;consumeMotion(zone,'exit');
assert.strictEqual(pendingMotion,null);assert.strictEqual(pendingMotionSince,0);
assert.strictEqual(sceneHandoffRequested,false);assert.strictEqual(lastMotion.seq,4);
assert.deepStrictEqual(events.filter(row=>row[0]==='request'),[['request',1],['request',0]]);
assert.strictEqual(motionAdmissionStats.lastAppliedSeq,4);
assert.strictEqual(motionAdmissionStats.supersededFrames,2);
assert.strictEqual(events.filter(row=>row[0]==='end').at(-1)[1],1);
const accepted=events.filter(row=>row[0]==='diagnostic').at(-1)[1];
assert.strictEqual(accepted.action,'accepted');assert.strictEqual(accepted.oldestPendingAgeMs,100);
assert.strictEqual(accepted.phase,'bootstrap');
assert.strictEqual(motionAdmissionStats.bootstrapPendingAgeMs,100);
assert.strictEqual(motionAdmissionStats.longestActivePendingAgeMs,0);
assert.deepStrictEqual(readSceneHandoffStats(),{requests:2,reservations:2,claims:1,abandoned:1,maximumReservationWaitMs:2,requestPending:1});
''')

    def test_actual_acceptance_rebases_age_to_first_newer_arrival_and_failure_does_not_clear_request(self):
        self.run_js(r'''
queueMotionFrame(frame(2));now=1080;consumeMotion(zone,'entry');
assert.strictEqual(sceneHandoffRequested,true);
acquired=1;applyFails=true;assert.throws(()=>consumeMotion(zone,'exit'),/pose failed/);
assert.strictEqual(pendingMotionSince,1000);assert.strictEqual(sceneHandoffRequested,true);
assert.strictEqual(motionAdmissionStats.acceptedFrames,0);
assert.strictEqual(events.filter(row=>row[0]==='end').at(-1)[1],0);
// Retrying is only a fixture: production failed commits terminate the private process.
applyFails=false;queueMotionFrame(frame(3));now=1100;
duringApply=frame(4,1095);consumeMotion(zone,'exit');
assert.strictEqual(lastMotion.seq,3);assert.strictEqual(pendingMotion.seq,4);
assert.strictEqual(pendingMotionSince,1095);assert.strictEqual(pendingMotionQueuedSince,1095);
assert.strictEqual(sceneHandoffRequested,false);
now=1130;queueMotionFrame(frame(5));assert.strictEqual(pendingMotionSince,1095);
now=1170;acquired=0;consumeMotion(zone,'ui');assert.strictEqual(sceneHandoffRequested,true);
now=1280;acquired=1;consumeMotion(zone,'exit');
assert.strictEqual(motionAdmissionStats.longestAcceptanceGapMs,180);
assert.strictEqual(pendingMotionSince,0);
''')

    def test_lifetime_age_keeps_bootstrap_while_active_age_begins_after_first_successful_acceptance(self):
        self.run_js(r'''
queueMotionFrame(frame(2));now=2652;
assert.strictEqual(motionAdmission().bootstrapPendingAgeMs,1652);
assert.strictEqual(motionAdmission().longestActivePendingAgeMs,0);
acquired=1;consumeMotion(zone,'exit');
assert.strictEqual(motionAdmissionStats.longestPendingAgeMs,1652);
assert.strictEqual(motionAdmissionStats.bootstrapPendingAgeMs,1652);
assert.strictEqual(motionAdmissionStats.longestActivePendingAgeMs,0);
assert.strictEqual(events.find(row=>row[0]==='diagnostic')[1].phase,'bootstrap');
now=2700;queueMotionFrame(frame(3));now=2794;
assert.strictEqual(motionAdmission().longestActivePendingAgeMs,94);
assert.strictEqual(motionAdmission().bootstrapPendingAgeMs,1652);
consumeMotion(zone,'exit');
assert.strictEqual(motionAdmissionStats.longestPendingAgeMs,1652);
assert.strictEqual(motionAdmissionStats.longestActivePendingAgeMs,94);
assert.strictEqual(events.filter(row=>row[0]==='diagnostic').at(-1)[1].phase,'active');
// Two successful accepts in one wall-clock millisecond are still active.
queueMotionFrame(frame(4,2694));consumeMotion(zone,'exit');
assert.strictEqual(motionAdmissionStats.longestActivePendingAgeMs,100);
assert.strictEqual(events.filter(row=>row[0]==='diagnostic').at(-1)[1].phase,'active');
''')

    def test_missing_optional_exports_preserves_legacy_gate_and_native_rejection_is_fatal(self):
        self.run_js(r'''
exportsAvailable=false;queueMotionFrame(frame(2));now=1100;consumeMotion(zone,'entry');
assert.strictEqual(pendingMotion.seq,2);assert.strictEqual(pendingMotionSince,1000);
assert.strictEqual(sceneHandoffRequested,false);assert(!events.some(row=>row[0]==='request'));
assert.deepStrictEqual(readSceneHandoffStats(),{});
// Rebind a fresh helper fixture with a rejecting export.
sceneHandoffBound=false;exportsAvailable=true;nativeResult=-1;
assert.throws(()=>consumeMotion(zone,'exit'),/handoff request failed: -1/);
assert.strictEqual(sceneHandoffRequested,false);assert.strictEqual(pendingMotion.seq,2);
assert.strictEqual(pendingMotionSince,1000);
sceneGateReady=false;sceneHandoffBound=false;events.length=0;
noteMotionDeferred(zone,'entry');assert.strictEqual(events.length,0);
assert.strictEqual(sceneHandoffBound,false);
''')


if __name__=='__main__':unittest.main()
