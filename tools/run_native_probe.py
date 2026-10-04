"""Launch a short, isolated, instrumented Reassembly diagnostic session.

Requires Frida in .runtime/python-tools and the compiled diagnostic DLL.
No normal save slot is opened. Shell user folders redirect to a private test
root; Steam initialization is disabled before game code runs. The process is
terminated after the bounded observation period. Only the known build is allowed.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
from pathlib import Path
import sys
import time
from research_control import ControlBridge
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.runtime' / 'python-tools'))
import frida

KNOWN_HASH = '8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c'


def create_campaign_fixture(sandbox,source,control_scheme='MOUSE_ROT',beam_fixture=False,player_ident=0x70000001):
    if control_scheme not in ('MOUSE_ROT','KEY_ROT','CARDINAL'):raise ValueError('Unknown native control scheme')
    ship=source.read_text().strip()
    if beam_fixture:
        # Test-only native stock weapon substitution; no installed file changes.
        replacement=ship.replace('{832, {38.651, 0}}','{845, {38.651, 0}}',1)
        if replacement==ship:raise ValueError('Beam fixture mount changed')
        ship=replacement
    if player_ident not in (0x70000001,0x70000002):raise ValueError('Unknown private player identity')
    tagged=ship.replace('{800, {-1.349, 0}}',f'{{800, {{-1.349, 0}}, command={{ident={player_ident:#x},faction=8}}}}',1)
    if tagged==ship: raise ValueError('Stock fixture command block changed')
    destination=sandbox/'Reassembly'/'data'/'save0'
    destination.mkdir(parents=True,exist_ok=False)
    data=f'{{version=2,faction=8,playerIdent={player_ident:#x},name="Multiplayer Research",points=1000,credits=1000,'+ \
        'progress=SPAWN_INTRO|BASIC_TUT|FIRST_RESPAWN|INTRO_1|INTRO_2,'+ \
        'metamap={{position={1000,0},flags=STATION|VISIBLE,ident=0x70000003,faction=100}},'+ \
        'controlScheme='+control_scheme+',position={0,0},mapTotalSize={60000,60000},blueprint='+tagged+',playerprint='+tagged+',blueprints={'+tagged+'}}'
    for name in ['save.lua','blueprints.lua']: (destination/name).write_text(data)
    (destination/'map1.lua').write_text('{radius={30000,30000},width=10,cells={'+
        ','.join('{200,0.5,0,0}' for _ in range(100))+'}}')

SCRIPT = r'''
const cfg = __CONFIG__;
const shell = Process.getModuleByName('SHELL32.dll');
const ole = Process.getModuleByName('ole32.dll');
const alloc = new NativeFunction(ole.getExportByName('CoTaskMemAlloc'), 'pointer', ['uint64']);
let redirects = 0;
Interceptor.attach(shell.getExportByName('SHGetKnownFolderPath'), {
  onEnter(args) { this.out = args[3]; },
  onLeave(retval) {
    if (retval.toInt32() < 0) throw new Error('Folder lookup failed');
    const replacement = alloc((cfg.sandbox.length + 1) * 2);
    replacement.writeUtf16String(cfg.sandbox);
    // The game owns and frees the replacement. The original allocation is tiny
    // and intentionally retained for this short-lived diagnostic process.
    this.out.writePointer(replacement); redirects++;
    send({type:'folder-redirect', root:cfg.sandbox});
  }
});
const legacy = shell.findExportByName('SHGetFolderPathW');
if (legacy) Interceptor.attach(legacy, {
  onEnter(args) { this.out = args[4]; },
  onLeave(retval) { if (retval.toInt32() >= 0) { this.out.writeUtf16String(cfg.sandbox); redirects++; } }
});
const steam = Process.getModuleByName('steam_api64.dll');
const noSteam = new NativeCallback(() => { send({type:'steam-disabled'}); return 0; }, 'int', []);
Interceptor.replace(steam.getExportByName('SteamAPI_Init'), noSteam);
const game = Process.getModuleByName('ReassemblyRelease.exe');
if(cfg.testQuietWorld && cfg.campaignRemote){
  // Isolate network motion in private recordings. These guards are never
  // selected by the launcher and do not change the production simulation.
  let blockedShots=0,blockedDamage=0;
  Interceptor.replace(game.getExportByName('?fireWeapon@Block@@QEAA_NAEAUFiringData@@@Z'),new NativeCallback(()=>{
    blockedShots++;return 0;
  },'bool',['pointer','pointer']));
  Interceptor.replace(game.getExportByName('?removeHealth@Block@@QEAAMMPEAU1@H@Z'),new NativeCallback(()=>{
    blockedDamage++;return 0;
  },'float',['pointer','float','pointer','int']));
  setInterval(()=>send({type:'quiet-world-fixture',blockedShots,blockedDamage}),1000);
}
let sampler = null, loaded = false, lastSample = 0, callbacks = 0, sampled = 0;
let mover = null, movesApplied = false;
const pendingMoves = [];
let coalescedControls=0,droppedControls=0,peakPendingControls=0;
let holdControlsUntil=0,receivedControls=0,controlHoldStarted=false;
let poseSetter = null, pendingState = null, stateSeq = -1;
let fire = null,fireShip=null,releaseWeapons=null;
let exporter = null, exported = false;
let clusterIdent = null;
let ensureIdent = null, nextIdent = 0x70000000;
const knownIdents = new Set();
let initialBindingsApplied=false;
let worldExporter=null, worldLoader=null, consoleContext=null, pendingWorld=null, worldSeq=-1;
let worldLoadMessage=null;
let replicaUpdater=null,replicaRemover=null,replicaReplacementRemover=null,replicaFragmentReplacementRemover=null,replicaBatchRemover=null,replicaBatchMessage=null,replicaStats=null,replicaRootCountReader=null;
let replicaFieldStatsReader=null,ownershipStatsReader=null,ownershipStatsBuffer=null,lastOwnershipReport=0;
let campaignMapExporter=null,campaignMapRemoteExporter=null,campaignMapApply=null,campaignMapExplore=null,campaignObjectivesApply=null,nativeMapRenders=0;
let replicaInitialized=false;
let campaignAuthorityZone=null,campaignAuthorityEnded=false,hostFlightState=null,hostMenuActive=false,hostMenuBusy=false,hostUpdateThread=null,hostMenuUpdates=0,hostMenuBrakes=0;
const campaignConsole=cfg.nativeCampaignClient?Memory.alloc(0x300):null;
let campaignReplicaZone=null;
const nativeWeaponTargets=new Map();
let nativeManualWeapons=[],nativeManualFeatures=[];
let nativeManualMaskKey='';
let motionRead=null,motionApply=null,motionBuffer=null,realtimeRead=null,realtimeBuffer=null,realtimeVisualApply=null,realtimeHealthApply=null,realtimeMoversApply=null,realtimeMoverWindowApply=null,localMoverCounter=null;
let wireHexDecode=null,wireHexEncode=null;
let predictionTick=null,predictionAck=null,predictionStats=null,realtimeHealthAudit=null,motionBegin=null,motionTimelineStats=null,motionApplyMessage=null,presentationRateReader=null,presentationGuardStats=null,framePaceStatsReader=null;
let localExhaustStatsReader=null,localExhaustStatsBuffer=null;
let replacementStatsReader=null,replacementStatsBuffer=null;
const sceneIdleGateEnabled=sceneGateSettings(cfg);
let sceneGateReady=false,sceneGateTryBegin=null,sceneGateEnd=null,sceneGateStatsReader=null,sceneGateStatsBuffer=null;
let visualBegin=null,viewSourceTimeReader=null,interpolationStatsReader=null,presentationClockReader=null,lastPresentationClock={},lastVisualFrame=null,lastHealthFrame=null,lastMoverWindow=null,visualFrameApplications=0;
const preparedVisualFrames=[];
let visualHistoryEpoch=0,visualHistoryResets=0;
const presentationDelayMs=cfg.presentationDelayMs===undefined?0:cfg.presentationDelayMs;
if(!Number.isFinite(presentationDelayMs)||presentationDelayMs<0||presentationDelayMs>200)throw new Error('Invalid presentation delay');
let predictionTestDriver=null,predictionTestAt=0;
let smoothFlightStarted=0,smoothFlightPhase=null,smoothFlightCycle=-1;
let lastMotionExport=0,motionSequence=0,pendingMotion=null,lastMotion=null,motionApplied=0,latestInputSequence=-1,latestInputTick=0;
let pendingMotionSince=0,pendingMotionQueuedSince=0,lastAcceptedMotionAt=0,sceneHandoffRequested=false;
let sceneHandoffBound=false,sceneHandoffRequest=null,sceneHandoffStatsReader=null,sceneHandoffStatsBuffer=null;
const motionAdmissionStats={preparedFrames:0,acceptedFrames:0,supersededFrames:0,deferredAttempts:0,
  longestPendingAgeMs:0,bootstrapPendingAgeMs:0,longestActivePendingAgeMs:0,longestAcceptanceGapMs:0,
  lastPreparedSeq:0,lastAppliedSeq:0,lastPreparedAtMs:0,lastAppliedAtMs:0};
let nativeZoneUpdates=0,nativeHeartbeatCalls=0;
let nativeDrawThread=null,nativeSwapThread=null,nativeCameraThread=null;
let motionTraceReader=null,motionTraceBuffer=null,lastMotionTrace=0;
const thrustAuditIdent=thrustAuditSettings(cfg.testThrustAudit);
let thrustAuditReader=null,thrustAuditBuffer=null,thrustAuditStatsBuffer=null,lastThrustAuditCenter=null,lastThrustAuditReport=0,thrustAuditBusySkips=0;
let particleRenderAuditReader=null,particleRenderAuditBuffer=null,lastParticleRenderAuditCalls=0,particleRenderAuditBusySkips=0;
let comparisonFocusReader=null,comparisonFocusBuffer=null;
const comparisonViewConfig=comparisonViewSettings(cfg.testComparisonView);
const comparisonFrameLimit=comparisonFrameLimitSettings(cfg.testComparisonFrameLimit,comparisonViewConfig);
let comparisonFocus=null,lastComparisonView=null;
const comparisonViewCounts={applied:0,held:0,busy:0,missing:0,focusReads:0,fallbacks:0};
let presentationStageClock=null,lastCameraPresentationStage=null,lastDrawPresentationStage=null;
function capturePresentationStage(kind){
  if(!cfg.measureMotion)return null;
  if(!presentationStageClock){
    const dll=Process.findModuleByName('RepopulatedDiagnostic.dll');if(!dll)return null;
    presentationStageClock=new NativeFunction(dll.getExportByName('RepopulatedMonotonicMillis'),'double',[]);
  }
  const stage={atMs:presentationStageClock(),frame:displayFrames,thread:Process.getCurrentThreadId()};
  if(kind==='camera')lastCameraPresentationStage=stage;else lastDrawPresentationStage=stage;
  return stage;
}
let hostAIStatsReader=null,hostAIStatsBuffer=null,hostAIStats=null,hostAIOwnedCallback=null,lastHostAIReport=0,hostAIOriginalCalls=0,vanillaEvidenceSent=false;
function reportHostAIStats(){
  if(!hostAIStatsReader || Date.now()-lastHostAIReport<500)return;
  lastHostAIReport=Date.now();hostAIStatsReader(hostAIStatsBuffer);
  const forwarded=hostAIStatsBuffer.add(8).readDouble();
  hostAIStats={total:hostAIStatsBuffer.readDouble(),nativeForwarded:forwarded,
               remoteDispatch:hostAIStatsBuffer.add(16).readDouble(),fallback:hostAIStatsBuffer.add(24).readDouble(),
               originalCalls:forwarded+hostAIOriginalCalls};
  send({type:'actor-control-ai-stats',...hostAIStats});
  if(!vanillaEvidenceSent && hostAIStats.originalCalls>=300){vanillaEvidenceSent=true;send({type:'vanilla-ai-preserved',calls:hostAIStats.originalCalls,source:'native-prefilter'});}
}
function readOwnershipStats(){
  if(!ownershipStatsReader)return {};
  ownershipStatsReader(ownershipStatsBuffer);
  const values=Array.from({length:4},(_,i)=>ownershipStatsBuffer.add(i*8).readDouble());
  return {sourceCommandlessCachedNonneutral:values[0],replicaCommandlessCachedNonneutral:values[1],
          sourceOwnedSerialCacheMismatch:values[2],replicaOwnedSerialCacheMismatch:values[3]};
}
function reportHostOwnershipStats(){
  if(!cfg.measureMotion || !cfg.campaignRemote || !ownershipStatsReader || Date.now()-lastOwnershipReport<500)return;
  lastOwnershipReport=Date.now();send({type:'ownership-audit',emittedAtMs:lastOwnershipReport,...readOwnershipStats()});
}
const nativeStageTimes=new Map(),nativeLastStages=new Map();
function nativeStage(stage,seq=null){
  if(!(cfg.traceNativeStages || cfg.measureMotion))return;
  const now=Date.now(),thread=Process.getCurrentThreadId(),record={stage,seq,enteredAtMs:now,thread};nativeLastStages.set(thread,record);
  if(!cfg.traceNativeStages && now-(nativeStageTimes.get(stage)||0)<200)return;
  nativeStageTimes.set(stage,now);send({type:'native-stage',...record});
}
const streamStats={motion:0,world:0,input:0,maxPrepareMs:0,maxApplyMs:0,zoneSkips:0};
const deliveryTimings={};
let lastMotionPrepared=0,lastMotionApplied=0,lastMotionSampled=0;
function timing(name,milliseconds){
  milliseconds=Math.max(0,milliseconds);
  let row=deliveryTimings[name];if(!row)row=deliveryTimings[name]={count:0,total:0,max:0,histogram:new Array(501).fill(0)};
  row.count++;row.total+=milliseconds;row.max=Math.max(row.max,milliseconds);row.histogram[Math.min(500,Math.floor(milliseconds))]++;
}
function deliverySummary(){
  const result={};for(const [name,row] of Object.entries(deliveryTimings)){
    let sum=0,p95=0;for(let i=0;i<row.histogram.length;i++){sum+=row.histogram[i];if(sum>=Math.ceil(row.count*0.95)){p95=i;break;}}
    result[name]={samples:row.count,meanMs:row.total/row.count,p95Ms:p95,maxMs:row.max};
  }return result;
}
let clientViewRadius=cfg.interestRadius || 5000,clientViewChangedAt=0;
let nativeFocused=true,nativeFlightActive=true,lastNativeIntent=null;
let testNativeMenuStage=0,testNativeMenuAt=0,testNativeModal=null;
let nativeFlightState=null,nativeUIHeartbeat=null,applyingFromMenu=false,menuSnapshotApplies=0,heartbeatBusy=false,nativeUpdateThread=null;
function sendNativeIntent(record) {
  record={...record,streamStats:{...streamStats,preparedMotion:motionSequence,appliedMotion:lastMotion?lastMotion.seq:0,pendingMotion:pendingMotion?pendingMotion.seq:0,
    oldestPendingAgeMs:pendingMotionSince?Math.max(0,Date.now()-pendingMotionSince):0}};
  if(cfg.measureMotion)record={...record,emittedAtMs:Date.now(),thread:Process.getCurrentThreadId()};
  lastNativeIntent=record;
  if((!nativeFocused && !cfg.testSmoothFlight) || !nativeFlightActive || openEditors.size)record={...record,dimensions:0x10a,destination:[0,0,0,0,0,0],
    weapons:record.weapons.map(row=>[row[0],0,...row.slice(2)]),inputBlocked:!nativeFocused?'focus':'menu'};
  send(record);
}
if(cfg.nativeCampaignClient)Interceptor.attach(game.getExportByName('?fireWeapon@Block@@QEAA_NAEAUFiringData@@@Z'),{
  onEnter(args){
    if(!clusterIdent)return;const block=args[0],cluster=block.add(0xb8).readPointer();
    if(cluster.isNull() || clusterIdent(cluster)!==cfg.followPilot)return;
    const data=args[1],position=[cluster.add(0x30).readDouble(),cluster.add(0x38).readDouble()];
    nativeWeaponTargets.set(block.add(0x30).readU32(),[data.add(8).readFloat()-position[0],data.add(12).readFloat()-position[1],data.add(24).readFloat(),data.add(28).readFloat(),data.add(52).readFloat()]);
  }
});
if(cfg.nativeCampaignClient){
  const target=game.base.add(0x1d09c0),prefix=[0x48,0x8b,0xc4,0x48,0x89,0x50,0x10,0x55,0x53,0x56];
  const actual=new Uint8Array(target.readByteArray(prefix.length));
  if(prefix.some((b,i)=>actual[i]!==b))throw new Error('Native manual weapon targeting signature differs');
  Interceptor.attach(target,{onEnter(args){this.player=args[0];},onLeave(){
    if(!clusterIdent)return;
    const command=this.player.add(0xa8).readPointer();if(command.isNull())return;
    const cluster=command.add(0xb8).readPointer();if(cluster.isNull() || clusterIdent(cluster)!==cfg.followPilot)return;
    const zone=this.player.add(0xf8).readPointer(),position=[cluster.add(0x30).readDouble(),cluster.add(0x38).readDouble()];
    const cursor=[zone.add(0x4c).readFloat()-position[0],zone.add(0x50).readFloat()-position[1],0,0,0];
    const begin=cluster.add(0xf0).readPointer(),count=cluster.add(0xf8).readPointer().sub(begin).toInt32()/8;
    if(count<0 || count>4096)throw new Error('Native manual weapon block limit');
    const weapons=[],features=[];
    for(let i=0;i<count;i++){
      const block=begin.add(i*8).readPointer(),f=block.add(0x40).readU64().and(uint64('0x800008e0')).toNumber();
      if(!(f&0x8e0))continue;
      const id=block.add(0x30).readU32(),enabled=block.add(0x100).readU64().and(uint64('0x800008e0')).toNumber();
      const turret=block.add(0x40).readU64().and(uint64(0x10)).toNumber()?block.add(0x158).readPointer():ptr(0);
      const aim=turret.isNull()?Math.atan2(cursor[1],cursor[0]):turret.add(0x10).readFloat();
      const values=nativeWeaponTargets.get(id) || cursor;
      weapons.push([id,enabled,...values.slice(0,4),turret.isNull()?values[4]:turret.add(0x14).readFloat(),aim]);features.push(f);
    }
    if(weapons.length>256)throw new Error('Native manual weapon limit');
    nativeManualWeapons=weapons;nativeManualFeatures=features;
    const key=weapons.map(row=>row[0]+':'+row[1]).join(',');
    if(key!==nativeManualMaskKey){
      nativeManualMaskKey=key;
      // A short native press can fit between regular 30 Hz flight samples.
      // Emit transitions at this verified native decision point as well.
      if(lastNativeIntent)sendNativeIntent({...lastNativeIntent,weapons,weaponFeatures:features});
    }
  }});
}
function installWireHex(dll){
  if(wireHexDecode && wireHexEncode)return;
  wireHexDecode=new NativeFunction(dll.getExportByName('RepopulatedDecodeHex'),'int',['pointer','int','pointer','int']);
  wireHexEncode=new NativeFunction(dll.getExportByName('RepopulatedEncodeHex'),'int',['pointer','int','pointer','int']);
}
function wireHexReady(){
  if(wireHexDecode && wireHexEncode)return true;
  const dll=Process.findModuleByName('RepopulatedDiagnostic.dll');
  if(!dll)return false;
  installWireHex(dll);return true;
}
function packedBuffer(hex){
  if(typeof hex!=='string' || hex.length%2 || hex.length>2*1048576)throw new Error('Invalid packed hex bounds');
  if(wireHexReady()){
    const size=hex.length/2,buffer=Memory.alloc(Math.max(1,size));
    const decoded=wireHexDecode(Memory.allocUtf8String(hex),hex.length,buffer,size);
    if(decoded!==size)throw new Error('Native packed hex decode failed: '+decoded);
    return buffer;
  }
  if(!/^[a-f0-9]*$/.test(hex))throw new Error('Invalid packed hex data');
  const data=[];for(let i=0;i<hex.length;i+=2)data.push(parseInt(hex.slice(i,i+2),16));
  const buffer=Memory.alloc(Math.max(1,data.length));if(data.length)buffer.writeByteArray(data);return buffer;
}
function hexBuffer(buffer,length){
  if(!Number.isInteger(length) || length<0 || length>1048576)throw new Error('Invalid native hex source bounds');
  if(wireHexReady()){
    const size=length*2,output=Memory.alloc(size+1);
    const encoded=wireHexEncode(buffer,length,output,size+1);
    if(encoded!==size)throw new Error('Native packed hex encode failed: '+encoded);
    return size?output.readUtf8String(size):'';
  }
  return Array.from(new Uint8Array(buffer.readByteArray(length)),b=>b.toString(16).padStart(2,'0')).join('');
}
function retainVisualFrame(frames,frame){
  const previous=frames[frames.length-1];
  if(previous && frame.sourceTimeMs-previous.sourceTimeMs>500){frames.length=0;visualHistoryEpoch++;visualHistoryResets++;}
  frame.visualEpoch=visualHistoryEpoch;frames.push(frame);
  if(frames.length>12)frames.shift();
}
function selectVisualFrame(frames,sourceViewTime){
  if(!frames.length)return null;
  let chosen=frames[0];
  for(const frame of frames){if(frame.sourceTimeMs>sourceViewTime)break;chosen=frame;}
  return chosen;
}
function currentVisualFrame(){
  if(!lastMotion || !realtimeHealthApply)return null;
  const eligible=preparedVisualFrames.filter(frame=>frame.seq<=lastMotion.seq && frame.visualEpoch===lastMotion.visualEpoch);
  const viewTime=presentationDelayMs>0?viewSourceTimeReader():0;
  const previous=lastVisualFrame && lastVisualFrame.visualEpoch===lastMotion.visualEpoch?lastVisualFrame:null;
  return presentationDelayMs>0?(viewTime<0&&previous?previous:selectVisualFrame(eligible,viewTime)):lastMotion;
}
function nextMoverFrame(frame){
  if(!frame || !lastMotion || frame.visualEpoch!==lastMotion.visualEpoch)return null;
  const next=preparedVisualFrames.find(candidate=>candidate.visualEpoch===frame.visualEpoch &&
    candidate.seq>frame.seq && candidate.seq<=lastMotion.seq);
  if(next && (!Number.isFinite(next.sourceTimeMs) || next.sourceTimeMs<frame.sourceTimeMs || next.sourceTimeMs-frame.sourceTimeMs>500))
    throw new Error('Invalid realtime mover window source time');
  return next || null;
}
function consumeVisualFrame(zone,restoreHealth=false){
  const frame=currentVisualFrame();
  if(!frame)return;
  const changed=!lastVisualFrame || frame.seq!==lastVisualFrame.seq;
  const useMoverWindow=cfg.nativeCampaignClient && cfg.fastMotion && realtimeMoverWindowApply!==null;
  const next=useMoverWindow?nextMoverFrame(frame):null;
  const windowChanged=useMoverWindow && (!lastMoverWindow || lastMoverWindow.epoch!==frame.visualEpoch ||
    lastMoverWindow.a!==frame.seq || lastMoverWindow.b!==(next?next.seq:0));
  if(!changed && !restoreHealth && !windowChanged)return;
  const started=Date.now();let stage=started;
  if(changed || restoreHealth){
    if(visualBegin)visualBegin(frame.sourceTimeMs);
    nativeStage('motion-health',frame.seq);
    if(realtimeHealthApply(zone,frame.health.buffer,frame.health.count)<0)throw new Error('Native realtime health apply failed');
    lastHealthFrame=frame;timing('healthApply',Date.now()-stage);stage=Date.now();
  }
  if(changed){
    nativeStage('motion-visuals',frame.seq);
    const applied=realtimeVisualApply(zone,frame.blocks.buffer,frame.blocks.count,frame.projectiles.buffer,frame.projectiles.count,ptr(0),0);
    if(applied<0)throw new Error('Native realtime visual apply failed: '+applied);
    timing('visualApply',Date.now()-stage);stage=Date.now();
  }
  if(useMoverWindow){
    const count=realtimeMoverWindowApply(frame.movers.buffer,frame.movers.count,frame.sourceTimeMs,
      next?next.movers.buffer:ptr(0),next?next.movers.count:0,next?next.sourceTimeMs:frame.sourceTimeMs);
    if(count!==frame.movers.count)throw new Error('Native realtime mover window apply failed: '+count);
    lastMoverWindow={a:frame.seq,b:next?next.seq:0,epoch:frame.visualEpoch};timing('moversApply',Date.now()-stage);
  }else if(changed){
    if(realtimeMoversApply(frame.movers.buffer,frame.movers.count)<0)throw new Error('Native realtime mover apply failed');
    lastMoverWindow=null;timing('moversApply',Date.now()-stage);
  }
  if(changed){lastVisualFrame=frame;visualFrameApplications++;}
  timing(restoreHealth?'visualFrameRestore':'visualFrameApply',Date.now()-started);
}
function applyMotionFrame(zone,frame,replay=true){
  const started=Date.now();
  if(replay){
    if(lastMotionApplied)timing('applyInterval',started-lastMotionApplied);lastMotionApplied=started;
    timing('receiveToApply',started-frame.received);
  }
  if(replay){
    nativeStage('motion-begin',frame.seq);
    if(motionBegin)motionBegin(frame.seq,Math.max(0,(started-frame.received)/1000),frame.sourceTimeMs,frame.simTimeMs);
  }
  if(predictionAck && replay)predictionAck(frame.inputTick,frame.seq);
  nativeStage('motion-poses',frame.seq);
  const count=motionApply(zone,frame.buffer,frame.count);
  if(count<0){
    const detail=motionApplyMessage?motionApplyMessage().readUtf8String():'native diagnostic unavailable';
    send({type:'motion-apply-failed',result:count,seq:frame.seq,replay,sourceTimeMs:frame.sourceTimeMs,simTimeMs:frame.simTimeMs,
          poseCount:frame.count,poses:hexBuffer(frame.buffer,frame.count*44),worldSeq,
          visualFrameSeq:lastVisualFrame?lastVisualFrame.seq:0,detail});
    throw new Error('Native motion apply failed: '+count+'; seq='+frame.seq+' replay='+replay+'; '+detail);
  }
  timing(replay?'posesApply':'posesRestore',Date.now()-started);
  if(count){if(replay)motionApplied++;replicaPresentedAt=frame.received;}
  streamStats.maxApplyMs=Math.max(streamStats.maxApplyMs,Date.now()-started);
  timing(replay?'motionPoseAccept':'motionRestore',Date.now()-started);
  if(replay)noteMotionAcceptance(zone,frame);
}
let interestExporter=null,displayFrames=0,lastPilotInput=0,drawCalls=0,pollCalls=0,lastView=null,replicaPilot=null,pilotRenderCalls=0;
let lastPresentation=0,longFrames=0,maxFrameMs=0;
let replicaPresenter=null,replicaPresentedAt=0,predictedFrames=0,presenterBusySkips=0,traceBusySkips=0;
let blockRenderCounter=null;
let visualExporter=null,visualApply=null,visualTick=null,visualProjectiles=null,visualStats=null,visualIdentities=null;
let projectilePass1Calls=0;
const presentedPilot=Memory.alloc(8);
const frameHistogram=new Array(251).fill(0);
function presentReplica(zone,stageName){
  const stage=capturePresentationStage(stageName);
  if(!replicaPresenter || !replicaPresentedAt)return;
  nativeStage('presenter');
  const predicted=replicaPresenter(zone,Math.min(0.25,Math.max(0,(Date.now()-replicaPresentedAt)/1000)),cfg.followPilot,presentedPilot);
  if(stage){stage.finishedAtMs=presentationStageClock();stage.result=predicted;}
  if(predicted===-7){presenterBusySkips++;return;} // Presentation skips a busy metadata writer.
  if(predicted<0)throw new Error('Replica presentation failed: '+predicted);
  if(predicted>0){replicaPilot=[presentedPilot.readFloat(),presentedPilot.add(4).readFloat()];predictedFrames++;}
}
function thrustAuditSettings(value){
  if(value===undefined || value===null)return 0;
  if(value!==0x70000002)throw new Error('Invalid native thrust audit fixture');
  return value;
}
function configureThrustAudit(dll){
  if(!thrustAuditIdent || thrustAuditReader)return;
  const configure=new NativeFunction(dll.getExportByName('RepopulatedConfigureThrustAudit'),'int',['uint','bool']);
  const configured=configure(thrustAuditIdent,Number(!cfg.renderOnly));
  if(configured<0)throw new Error('Native thrust audit configuration failed: '+configured);
  thrustAuditReader=new NativeFunction(dll.getExportByName('RepopulatedReadThrustAudit'),'int',['pointer','int','pointer']);
  thrustAuditBuffer=Memory.alloc(128*28*8);thrustAuditStatsBuffer=Memory.alloc(4*8);
  const particleRender=game.base.add(0x458c0),prefix=[0x48,0x89,0x5c,0x24,0x20,0x4c,0x89,0x44,0x24,0x18,0x48,0x89,0x54,0x24,0x10];
  const actual=new Uint8Array(particleRender.readByteArray(prefix.length));
  if(prefix.some((byte,i)=>actual[i]!==byte))throw new Error('Native particle render audit signature differs');
  const original=Interceptor.replaceFast(particleRender,dll.getExportByName('RepopulatedAuditParticleRender'));
  try{new NativeFunction(dll.getExportByName('RepopulatedSetParticleRenderAuditOriginal'),'void',['pointer'])(original);}
  catch(error){Interceptor.revert(particleRender);throw error;}
  particleRenderAuditReader=new NativeFunction(dll.getExportByName('RepopulatedReadParticleRenderAudit'),'int',['pointer']);
  particleRenderAuditBuffer=Memory.alloc(18*8);
}
function installHostThrustAudit(dll){
  if(!thrustAuditIdent || cfg.renderOnly)return;
  configureThrustAudit(dll);
  const mover=game.base.add(0xf09e0),prefix=[0x48,0x8b,0xc4,0x53,0x57,0x48,0x81,0xec,0x08,0x01,0x00,0x00];
  const actual=new Uint8Array(mover.readByteArray(prefix.length));
  if(prefix.some((byte,i)=>actual[i]!==byte))throw new Error('Native audit mover signature differs');
  const original=Interceptor.replaceFast(mover,dll.getExportByName('RepopulatedAuditMoverUpdate'));
  try{new NativeFunction(dll.getExportByName('RepopulatedSetAuditMoverOriginal'),'void',['pointer'])(original);}
  catch(error){Interceptor.revert(mover);throw error;}
}
function reportThrustAudit(center=null){
  if(!thrustAuditReader)return;
  reportParticleRenderAudit();
  if(center)lastThrustAuditCenter=center;
  const count=thrustAuditReader(thrustAuditBuffer,128,thrustAuditStatsBuffer);
  if(count===-7){thrustAuditBusySkips++;return;}
  if(count<0 || count>128)throw new Error('Native thrust audit read failed: '+count);
  const values=Array.from({length:4},(_,i)=>thrustAuditStatsBuffer.add(i*8).readDouble());
  if(values.some(value=>!Number.isFinite(value)||value<0) || values[3]!==28)throw new Error('Invalid native thrust audit stats');
  if(!count && Date.now()-lastThrustAuditReport<500)return;
  const rows=Array.from({length:count},(_,row)=>Array.from({length:28},(_,column)=>thrustAuditBuffer.add((row*28+column)*8).readDouble()));
  if(rows.some(row=>row.some(value=>!Number.isFinite(value)) || row[1]!==thrustAuditIdent || !Number.isInteger(row[2]) || row[2]<=0 || row[2]>0xffffffff))
    throw new Error('Invalid native thrust audit row');
  lastThrustAuditReport=Date.now();
  send({type:'thrust-audit',emittedAtMs:lastThrustAuditReport,frame:displayFrames,thread:Process.getCurrentThreadId(),
        center:lastThrustAuditCenter,rows,presentationDelayMs,
        stats:{total:values[0],dropped:values[1],pending:values[2],stride:values[3],busySkips:thrustAuditBusySkips}});
}
function reportParticleRenderAudit(){
  if(!particleRenderAuditReader)return;
  const ready=particleRenderAuditReader(particleRenderAuditBuffer);
  if(ready===-7){particleRenderAuditBusySkips++;return;}
  if(ready===0)return;
  if(ready!==1)throw new Error('Native particle render audit read failed: '+ready);
  const row=Array.from({length:18},(_,column)=>particleRenderAuditBuffer.add(column*8).readDouble());
  if(row.some(value=>!Number.isFinite(value)))throw new Error('Invalid native particle render audit row');
  if(row[15]===lastParticleRenderAuditCalls)return;
  lastParticleRenderAuditCalls=row[15];
  send({type:'particle-render-audit',emittedAtMs:Date.now(),frame:displayFrames,thread:Process.getCurrentThreadId(),
        row,busySkips:particleRenderAuditBusySkips});
}
function readLocalExhaustStats(){
  if(!localExhaustStatsReader)return {};
  localExhaustStatsReader(localExhaustStatsBuffer);
  const labels=['transformed','snapshotUnavailable','staleCurveSkipped','publicationMisses','invalidRootSkipped'];
  const values=labels.map((label,i)=>localExhaustStatsBuffer.add(i*8).readDouble());
  if(values.some(value=>!Number.isFinite(value)||value<0))throw new Error('Invalid native local exhaust stats');
  return Object.fromEntries(labels.map((label,i)=>[label,values[i]]));
}
function sceneGateSettings(config){
  for(const key of ['sceneIdleGate','testSceneIdleGate'])
    if(config[key]!==undefined && typeof config[key]!=='boolean')throw new Error('Scene idle gate settings must be boolean');
  if(config.sceneIdleGate!==true && config.testSceneIdleGate!==true)return false;
  if(!config.nativeCampaignClient || !config.persistentReplica || !config.fastMotion || config.frameLimit!==60)
    throw new Error('Scene idle gate requires the native campaign client');
  return true;
}
function readReplacementStats(){
  if(!replacementStatsReader)return {};
  replacementStatsReader(replacementStatsBuffer);
  const labels=['ownedDetached','neutralDetached','ownedBound','neutralBound','ownedRejected','neutralRejected','unannouncedResets','ordinaryResets'];
  const values=labels.map((label,i)=>replacementStatsBuffer.add(i*8).readDouble());
  if(values.some(value=>!Number.isFinite(value)||value<0))throw new Error('Invalid native replacement stats');
  return Object.fromEntries(labels.map((label,i)=>[label,values[i]]));
}
function configureSceneGate(zone){
  if(!sceneIdleGateEnabled || sceneGateReady)return;
  if(!replicaInitialized || !campaignReplicaZone || !zone.equals(campaignReplicaZone) || nativeUpdateThread!==Process.getCurrentThreadId())
    throw new Error('Native scene gate configuration requires the verified replica update thread');
  if(nativeDrawThread===null)return; // Wait for a real native draw before identifying its execution model.
  if(nativeDrawThread===nativeUpdateThread)throw new Error('Native scene idle gate requires distinct draw and update threads');
  const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
  const configure=new NativeFunction(dll.getExportByName('RepopulatedConfigureSceneGate'),'int',['pointer']);
  sceneGateTryBegin=new NativeFunction(dll.getExportByName('RepopulatedTryBeginSceneUpdate'),'int',['pointer']);
  sceneGateEnd=new NativeFunction(dll.getExportByName('RepopulatedEndSceneUpdate'),'int',['pointer','int']);
  sceneGateStatsReader=new NativeFunction(dll.getExportByName('RepopulatedSceneGateStats'),'void',['pointer']);
  sceneGateStatsBuffer=Memory.alloc(8*8);
  // The next Render must close the idle window before it acquires engine
  // state locks. Keeping the window through swap permits post-swap update
  // callbacks to accept motion without waiting for accidental phase drift.
  const boundary=game.base.add(0x118730),prefix=[0x48,0x8b,0xc4,0x55,0x53,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57];
  const actual=new Uint8Array(boundary.readByteArray(prefix.length));
  if(prefix.some((byte,i)=>actual[i]!==byte))throw new Error('Native scene render boundary signature differs');
  const replacement=dll.getExportByName('RepopulatedRenderBoundary');
  const setOriginal=new NativeFunction(dll.getExportByName('RepopulatedSetRenderBoundaryOriginal'),'void',['pointer']);
  const original=Interceptor.replaceFast(boundary,replacement);
  try{
    setOriginal(original);
    const configured=configure(zone);if(configured!==1)throw new Error('Native scene gate configuration failed: '+configured);
  }catch(error){Interceptor.revert(boundary);throw error;}
  sceneGateReady=true;send({type:'native-scene-gate',action:'configured',thread:Process.getCurrentThreadId()});
}
function trySceneUpdate(zone){
  if(!sceneGateReady)return 2; // Initial import and legacy sessions use the existing path.
  if(!zone.equals(campaignReplicaZone) || nativeUpdateThread!==Process.getCurrentThreadId())
    throw new Error('Native scene update requires the verified replica update thread');
  const acquired=sceneGateTryBegin(zone);
  if(acquired!==0 && acquired!==1)throw new Error('Native scene gate acquisition failed: '+acquired);
  return acquired;
}
function endSceneUpdate(zone,token,committed){
  if(token!==1)return;
  const ended=sceneGateEnd(zone,Number(committed));
  if(ended!==1)throw new Error('Native scene gate release failed: '+ended);
}
function readSceneGateStats(){
  if(!sceneGateStatsReader)return {};
  sceneGateStatsReader(sceneGateStatsBuffer);
  const labels=['acquired','deferred','transactions','maximumTransactionMs','renderWaits','maximumRenderWaitMs','timeouts','state'];
  const values=labels.map((label,i)=>sceneGateStatsBuffer.add(i*8).readDouble());
  if(values.some(value=>!Number.isFinite(value)||value<0))throw new Error('Invalid native scene gate stats');
  return Object.fromEntries(labels.map((label,i)=>[label,values[i]]));
}
function bindSceneHandoff(){
  if(sceneHandoffBound || !sceneGateReady)return;
  sceneHandoffBound=true;
  const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
  const request=dll.findExportByName('RepopulatedRequestSceneHandoff');
  const stats=dll.findExportByName('RepopulatedSceneHandoffStats');
  // Older helpers retain the existing nonblocking gate without a reservation.
  if(!request)return;
  sceneHandoffRequest=new NativeFunction(request,'int',['pointer','int']);
  if(stats){sceneHandoffStatsReader=new NativeFunction(stats,'void',['pointer']);sceneHandoffStatsBuffer=Memory.alloc(48);}
}
function queueMotionFrame(frame){
  const now=Date.now();frame.preparedAtMs=now;
  if(pendingMotion)motionAdmissionStats.supersededFrames++;
  else pendingMotionQueuedSince=frame.received;
  if(!pendingMotionSince)pendingMotionSince=pendingMotionQueuedSince;
  pendingMotion=frame;
  motionAdmissionStats.preparedFrames++;motionAdmissionStats.lastPreparedSeq=frame.seq;motionAdmissionStats.lastPreparedAtMs=now;
}
function motionAdmission(){
  const age=pendingMotionSince?Math.max(0,Date.now()-pendingMotionSince):0;
  motionAdmissionStats.longestPendingAgeMs=Math.max(motionAdmissionStats.longestPendingAgeMs,age);
  const label=motionAdmissionStats.acceptedFrames?'longestActivePendingAgeMs':'bootstrapPendingAgeMs';
  motionAdmissionStats[label]=Math.max(motionAdmissionStats[label],age);
  return {...motionAdmissionStats,pendingSinceMs:pendingMotionSince,pendingAgeMs:age,pendingSeq:pendingMotion?pendingMotion.seq:0};
}
function reportMotionAdmission(action,zone,frame,age,gap=0,opportunity=null,bootstrap=motionAdmissionStats.acceptedFrames===0){
  if(!cfg.measureMotion)return;
  send({type:'native-motion-admission',action,emittedAtMs:Date.now(),thread:Process.getCurrentThreadId(),
    phase:bootstrap?'bootstrap':'active',
    opportunity,seq:frame.seq,receivedAtMs:frame.received,preparedAtMs:frame.preparedAtMs,
    sourceTimeMs:frame.sourceTimeMs,oldestPendingAgeMs:age,acceptanceGapMs:gap,
    appliedSeq:motionAdmissionStats.lastAppliedSeq,preparedSeq:motionAdmissionStats.lastPreparedSeq,sceneGate:readSceneGateStats()});
}
function noteMotionDeferred(zone,opportunity){
  if(!pendingMotion || !sceneGateReady)return;
  const age=motionAdmission().pendingAgeMs;motionAdmissionStats.deferredAttempts++;
  if(age<75 || sceneHandoffRequested)return;
  if(!zone.equals(campaignReplicaZone) || Process.getCurrentThreadId()!==nativeUpdateThread)
    throw new Error('Native scene handoff requires the verified replica update thread');
  bindSceneHandoff();
  if(!sceneHandoffRequest)return;
  const requested=sceneHandoffRequest(zone,1);
  if(requested!==1)throw new Error('Native scene handoff request failed: '+requested);
  sceneHandoffRequested=true;reportMotionAdmission('requested',zone,pendingMotion,age,0,opportunity);
}
function noteMotionAcceptance(zone,frame){
  const now=Date.now(),age=pendingMotionSince?Math.max(0,now-pendingMotionSince):0;
  const gap=lastAcceptedMotionAt?Math.max(0,now-lastAcceptedMotionAt):0;
  const bootstrap=motionAdmissionStats.acceptedFrames===0;
  motionAdmissionStats.longestPendingAgeMs=Math.max(motionAdmissionStats.longestPendingAgeMs,age);
  const label=bootstrap?'bootstrapPendingAgeMs':'longestActivePendingAgeMs';
  motionAdmissionStats[label]=Math.max(motionAdmissionStats[label],age);
  motionAdmissionStats.longestAcceptanceGapMs=Math.max(motionAdmissionStats.longestAcceptanceGapMs,gap);
  motionAdmissionStats.acceptedFrames++;motionAdmissionStats.lastAppliedSeq=frame.seq;motionAdmissionStats.lastAppliedAtMs=now;lastAcceptedMotionAt=now;
  // An RPC can prepare another frame while a cooperative native call applies
  // this one. Its first queued arrival becomes the new oldest unserved age.
  pendingMotionSince=pendingMotion?pendingMotionQueuedSince:0;
  if(!pendingMotion)pendingMotionQueuedSince=0;
  if(sceneHandoffRequested){
    const cleared=sceneHandoffRequest(zone,0);
    if(cleared!==1)throw new Error('Native scene handoff reset failed: '+cleared);
    sceneHandoffRequested=false;
  }
  if(age>=75 || gap>=150)reportMotionAdmission('accepted',zone,frame,age,gap,null,bootstrap);
}
function readSceneHandoffStats(){
  bindSceneHandoff();
  if(!sceneHandoffStatsReader)return {};
  sceneHandoffStatsReader(sceneHandoffStatsBuffer);
  const labels=['requests','reservations','claims','abandoned','maximumReservationWaitMs','requestPending'];
  const values=labels.map((label,i)=>sceneHandoffStatsBuffer.add(i*8).readDouble());
  if(values.some(value=>!Number.isFinite(value)||value<0))throw new Error('Invalid native scene handoff stats');
  return Object.fromEntries(labels.map((label,i)=>[label,values[i]]));
}
function installReplicaField(dll,zone){
  if(!cfg.nativeCampaignClient)return;
  const getter=game.base.add(0xcab60),prefix=[0x40,0x53,0x56,0x57,0x48,0x83,0xec,0x30,0xc5,0xf8,0x29,0x74,0x24,0x20];
  const actual=new Uint8Array(getter.readByteArray(prefix.length));
  if(prefix.some((byte,i)=>actual[i]!==byte))throw new Error('Native replica field signature differs');
  const original=Interceptor.replaceFast(getter,dll.getExportByName('RepopulatedReplicaField'));
  try{
    new NativeFunction(dll.getExportByName('RepopulatedSetFieldOriginal'),'void',['pointer'])(original);
    const configured=new NativeFunction(dll.getExportByName('RepopulatedConfigureReplicaField'),'int',['pointer','pointer'])(campaignConsole,zone);
    if(configured<0)throw new Error('Native replica field configuration failed: '+configured);
    replicaFieldStatsReader=new NativeFunction(dll.getExportByName('RepopulatedReplicaFieldStats'),'void',['pointer']);
  }catch(error){Interceptor.revert(getter);throw error;}
}
function replicaContinuity(plan){
  if(!plan || !Array.isArray(plan.remove) || plan.remove.length>4096 || new Set(plan.remove).size!==plan.remove.length ||
     plan.remove.some(ident=>!Number.isInteger(ident) || ident<=0 || ident>0xffffffff))
    throw new Error('Invalid incremental removals');
  const rows=plan.continuity===undefined?[]:plan.continuity;
  if(!Array.isArray(rows)||rows.length>plan.remove.length||
     new Set(rows.map(row=>Array.isArray(row)?row[0]:null)).size!==rows.length||
     rows.some(row=>!Array.isArray(row)||![3,4].includes(row.length)||row.some(value=>!Number.isInteger(value))||
       row[0]<=0||row[0]>0xffffffff||!plan.remove.includes(row[0])||row[1]<=0||row[1]>0xffffffff||row[2]<0||row[2]>0x7fffffff||
       row.length===4 && (row[3]!==2 || row[1]!==row[0] || row[2]!==0)))
    throw new Error('Invalid incremental continuity');
  return new Map(rows.map(row=>[row[0],row]));
}
function applyReplicaRemovals(zone,plan,seq=null){
  const continuity=replicaContinuity(plan);
  if(!plan.remove.length)return 0;
  if(cfg.nativeCampaignClient && sceneGateReady && replicaBatchRemover){
    const identities=[...plan.remove].sort((a,b)=>a-b),rows=Memory.alloc(identities.length*16);
    identities.forEach((ident,index)=>{
      const requested=continuity.get(ident),generation=requested && (requested.length===3 || replicaFragmentReplacementRemover)?requested:null,row=rows.add(index*16);
      row.writeU32(ident);row.add(4).writeU32(generation?generation[1]:0);
      row.add(8).writeS32(generation?generation[2]:0);row.add(12).writeU32(generation?(generation.length===4?2:1):0);
    });
    const removed=replicaBatchRemover(zone,rows,identities.length);
    if(!Number.isInteger(removed) || removed<0 || removed>identities.length){
      const detail=replicaBatchMessage?replicaBatchMessage().readUtf8String():'native diagnostic unavailable';
      send({type:'replica-removal-failed',seq,result:removed,remove:identities,continuity:[...continuity.values()],detail});
      throw new Error('Replica batch removal failed: '+removed+' count='+identities.length+' seq='+seq+'; '+detail);
    }
    return removed;
  }
  for(const ident of plan.remove){
    const generation=continuity.get(ident);
    const removed=generation && generation.length===3?replicaReplacementRemover(zone,ident,generation[1],generation[2]):replicaRemover(zone,ident);
    if(removed<0)throw new Error('Replica removal failed: '+removed+' id=0x'+ident.toString(16));
  }
}
function comparisonViewSettings(value){
  if(value===undefined || value===null)return null;
  if(typeof value!=='object' || Array.isArray(value) || Object.keys(value).sort().join(',')!=='ident,zoom' ||
     ![0x70000001,0x70000002].includes(value.ident) || !Number.isFinite(value.zoom) ||
     !(Math.fround(value.zoom)>0) || !Number.isFinite(Math.fround(value.zoom)))throw new Error('Invalid comparison view fixture');
  return {ident:value.ident,zoom:Math.fround(value.zoom)};
}
function comparisonFrameLimitSettings(value,view){
  if(value===undefined || value===false)return false;
  if(value!==true || !view)throw new Error('Comparison frame limit requires the comparison view fixture');
  return true;
}
function ensureMotionTraceReader(){
  if(!motionTraceReader){
    motionTraceReader=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPresentedMotionTrace'),'int',['pointer','pointer']);
    motionTraceBuffer=Memory.alloc(42*8);
  }
}
function applyComparisonView(zone,view,context){
  if(!comparisonViewConfig || !loaded)return view;
  const viewport=Array.from({length:4},(_,i)=>view.add(i*4).readFloat());
  if(viewport.some(value=>!Number.isFinite(value) || value<=0))throw new Error('Invalid comparison view viewport');
  ensureMotionTraceReader();
  const count=motionTraceReader(zone,motionTraceBuffer);let status='target',focus=null;
  if(count===-7){
    comparisonViewCounts.busy++;status='busy';
  }
  else if(count<0)throw new Error('Native comparison view trace failed: '+count);
  else {
    for(let row=0;count>0 && row<2;row++){
      const data=motionTraceBuffer.add(row*20*8);if(data.readDouble()!==comparisonViewConfig.ident)continue;
      const x=data.add(16*8).readDouble()+motionTraceBuffer.add(40*8).readDouble();
      const y=data.add(17*8).readDouble()+motionTraceBuffer.add(41*8).readDouble();
      if(!Number.isFinite(x) || !Number.isFinite(y))throw new Error('Invalid comparison view render position');
      focus={x,y,atMs:data.add(2*8).readDouble()};break;
    }
    if(!focus){comparisonViewCounts.missing++;status='missing';}
  }
  if(!focus){
    // Trace freshness can hide a live root when source poses stall. Keep the
    // private comparison camera on a freshly validated actual render point;
    // otherwise falling back to the native camera manufactures a zoom/pan
    // discontinuity in the recording. A genuinely absent root stays native.
    if(!comparisonFocusReader){
      const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
      comparisonFocusReader=new NativeFunction(dll.getExportByName('RepopulatedReadComparisonFocus'),'int',['pointer','uint','pointer']);
      comparisonFocusBuffer=Memory.alloc(16);
      if(!presentationStageClock)presentationStageClock=new NativeFunction(dll.getExportByName('RepopulatedMonotonicMillis'),'double',[]);
    }
    comparisonViewCounts.focusReads++;
    const result=comparisonFocusReader(zone,comparisonViewConfig.ident,comparisonFocusBuffer);
    if(result===1){
      const x=comparisonFocusBuffer.readDouble(),y=comparisonFocusBuffer.add(8).readDouble();
      if(!Number.isFinite(x) || !Number.isFinite(y))throw new Error('Invalid comparison view fallback render position');
      focus={x,y,atMs:presentationStageClock()};status='target-'+status;
    }else if(result!==0)throw new Error('Native comparison focus failed: '+result);
  }
  comparisonFocus=focus;
  if(!focus){
    comparisonViewCounts.fallbacks++;
    context.comparisonView=lastComparisonView={ident:comparisonViewConfig.ident,status:'fallback-native-'+status,applied:false,
        x:view.add(0x10).readFloat(),y:view.add(0x14).readFloat(),zoom:view.add(0x20).readFloat(),
        orientation:[view.add(0x28).readFloat(),view.add(0x2c).readFloat()],viewport,
        frameLimit:comparisonFrameLimit?60:null,counts:{...comparisonViewCounts}};
    return view;
  }
  const copy=Memory.alloc(72);copy.writeByteArray(view.readByteArray(72));
  copy.add(0x10).writeFloat(comparisonFocus.x);copy.add(0x14).writeFloat(comparisonFocus.y);
  copy.add(0x18).writeFloat(0);copy.add(0x1c).writeFloat(0);copy.add(0x20).writeFloat(comparisonViewConfig.zoom);
  copy.add(0x28).writeFloat(1);copy.add(0x2c).writeFloat(0);
  comparisonViewCounts.applied++;context.spectatorView=copy;
  context.comparisonView=lastComparisonView={ident:comparisonViewConfig.ident,status,applied:true,x:copy.add(0x10).readFloat(),y:copy.add(0x14).readFloat(),
      zoom:copy.add(0x20).readFloat(),orientation:[copy.add(0x28).readFloat(),copy.add(0x2c).readFloat()],
      sourceAtMs:comparisonFocus.atMs,viewport,frameLimit:comparisonFrameLimit?60:null,counts:{...comparisonViewCounts}};
  return copy;
}
function tracePresentation(zone,view,comparisonFrame=null){
  if(!(cfg.testSmoothFlight || cfg.measureMotion || thrustAuditIdent) || !loaded || Date.now()-lastMotionTrace<33)return;
  lastMotionTrace=Date.now();ensureMotionTraceReader();
  const count=motionTraceReader(zone,motionTraceBuffer);if(count===-7){traceBusySkips++;reportThrustAudit();return;}if(count<0)throw new Error('Native motion trace failed');
  const center=[motionTraceBuffer.add(40*8).readDouble(),motionTraceBuffer.add(41*8).readDouble()];reportThrustAudit(center);
  if(!count || !(cfg.testSmoothFlight || cfg.measureMotion))return;
  const rows=Array.from({length:2},(_,row)=>Array.from({length:20},(_,column)=>motionTraceBuffer.add((row*20+column)*8).readDouble()));
  send({type:'presentation-trace',emittedAtMs:Date.now(),frame:displayFrames,thread:Process.getCurrentThreadId(),
        simTimeSeconds:zone.add(0x158).readFloat(),view:{x:view.add(0x10).readFloat(),y:view.add(0x14).readFloat(),zoom:view.add(0x20).readFloat()},
        center,rows,presentationDelayMs,visualFrameSeq:lastVisualFrame?lastVisualFrame.seq:0,healthFrameSeq:lastHealthFrame?lastHealthFrame.seq:0,
        viewSourceTimeMs:viewSourceTimeReader?viewSourceTimeReader():null,
        comparisonView:comparisonFrame,
        presentationStages:cfg.measureMotion?{camera:lastCameraPresentationStage,draw:lastDrawPresentationStage}:null,
        fixture:cfg.testSmoothFlight?{phase:smoothFlightPhase,cycle:smoothFlightCycle,elapsedSeconds:smoothFlightStarted?(Date.now()-smoothFlightStarted)/1000:0}:null});
}
const pilotKeys=new Set();
let mouseOffset=null,mouseHeld=false,mouseX=0,mouseY=0,mouseWindow=null,getWindow=null,getWindowSize=null;
let textInputActive=null;
const openEditors=new Set();
if(cfg.nativeCampaignClient){
  // The stock camera follows Body's render point. Prepare that point before
  // its follow routine computes the View, rather than only in DrawGame after
  // the camera has already read a newly received authoritative pose.
  const cameraFollow=game.base.add(0xf51e0),cameraPrefix=[0x48,0x8b,0xc4,0x48,0x89,0x58,0x10,0x48,0x89,0x70,0x18];
  const cameraActual=new Uint8Array(cameraFollow.readByteArray(cameraPrefix.length));
  if(cameraPrefix.some((byte,i)=>cameraActual[i]!==byte))throw new Error('Native camera follow signature differs');
  Interceptor.attach(cameraFollow,{onEnter(args){
    if(!campaignReplicaZone || !args[1].equals(campaignReplicaZone))return;
    nativeCameraThread=Process.getCurrentThreadId();
    if(cfg.fastMotion && nativeUpdateThread===Process.getCurrentThreadId())consumeMotion(args[1]);
    presentReplica(args[1],'camera');
  }});
  Interceptor.attach(game.base.add(0xa77f0),{onEnter(){nativeMapRenders++;}});
  const markPrefix=[0x48,0x8b,0xc4,0x48,0x89,0x58,0x18,0x55,0x56,0x57];
  const markActual=new Uint8Array(game.base.add(0xfea60).readByteArray(markPrefix.length));
  if(markPrefix.some((b,i)=>markActual[i]!==b))throw new Error('Native exploration ABI differs');
  // Rendering the stock minimap also reveals local cells. The replica must
  // use only the host's exploration, never its extrapolated camera position.
  Interceptor.replace(game.base.add(0xfea60),new NativeCallback(()=>0,'int',['pointer','uint64','float']));
  // This exact-build helper handles only the remapped constructor shortcut.
  // Local editor commits cannot yet be submitted as authoritative transactions.
  const prefix=[0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10];
  const actual=new Uint8Array(game.base.add(0x11a0e0).readByteArray(prefix.length));
  if(!prefix.every((b,i)=>actual[i]===b))throw new Error('Native constructor shortcut ABI differs');
  const bindings=new NativeFunction(game.base.add(0x3dd20),'pointer',[]);
  const input=new NativeFunction(game.base.add(0x1aac0),'pointer',['int']);
  const eventKey=new NativeFunction(game.base.add(0x19bf0),'int',['pointer','pointer']);
  const topState=new NativeFunction(game.base.add(0x119f90),'bool',['pointer']);
  let lastNotice=0;
  function unavailable(){
    if(Date.now()-lastNotice>2000){lastNotice=Date.now();send({type:'native-client-notice',message:'Remote building, upgrades and fleet changes are not synchronized yet. These actions are disabled in this flight/combat build.'});}
  }
  Interceptor.replace(game.base.add(0x11a0e0),new NativeCallback((state,event)=>{
    if(game.base.add(0x3cf417).readU8())return 0;
    const key=eventKey(input(-1),event);if(!key)return 0;
    const keys=bindings();
    for(let i=0;i<4;i++)if(keys.add(0xc68+i*4).readS32()===key){
      unavailable();
      return 1;
    }
    return 0;
  },'bool',['pointer','pointer']));
  const constructPrefix=[0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x6c,0x24,0x10];
  const constructActual=new Uint8Array(game.base.add(0x1d3100).readByteArray(constructPrefix.length));
  if(!constructPrefix.every((b,i)=>constructActual[i]===b))throw new Error('Native campaign menu ABI differs');
  let menuForward=null;
  const originalMenu=Interceptor.replaceFast(game.base.add(0x1d3100),new NativeCallback((player,event,state)=>{
    const key=eventKey(input(-1),event),keys=bindings();
    if(key && topState(state))for(const offset of [0xa58,0xa00,0x740])for(let i=0;i<4;i++)if(keys.add(offset+i*4).readS32()===key){unavailable();return 1;}
    return menuForward(player,event,state);
  },'bool',['pointer','pointer','pointer']));
  menuForward=new NativeFunction(originalMenu,'bool',['pointer','pointer','pointer']);
  // Map and Binding keep their native tabs; remove unsynchronized Upgrade/Fleet.
  Interceptor.attach(game.base.add(0x27fe90),{onEnter(args){args[2]=ptr(args[2].toUInt32()&~0x28);}});
  Interceptor.attach(game.base.add(0x281920),{onEnter(args){
    if(nativeFlightState && args[0].equals(nativeFlightState)){
      nativeFlightState=null;nativeFlightActive=false;if(lastNativeIntent)sendNativeIntent(lastNativeIntent);
      send({type:'error',description:'Joining game left its campaign. Start Join game again to reconnect.'});
    }
  }});
  // GSFly's native update distinguishes the active flight state from overlays.
  nativeUIHeartbeat=()=>{
    nativeHeartbeatCalls++;
    if(!nativeFlightState || heartbeatBusy)return;
    heartbeatBusy=true;
    try{
    const state=nativeFlightState;
    if(cfg.testNativeMenus && worldSeq>=20){
      if([0,2].includes(testNativeMenuStage) && topState(state)){
        const tab=testNativeMenuStage===0?1:0x10;
        const show=new NativeFunction(game.base.add(0xb6f20),'uint64',['pointer','uint','uint']);
        if(show(state,tab,tab).toNumber()!==1)throw new Error('Native menu test could not open tab');
        const list=state.add(0x10).readPointer();
        testNativeModal=list.add(8).readPointer().add(list.add(0x70).readS32()*8).readPointer();
        if(testNativeModal.equals(state))throw new Error('Native menu test did not activate overlay');
        testNativeMenuAt=Date.now();testNativeMenuStage++;
        send({type:'native-menu-test',action:'opened',tab,displayFrames,nativeMapRenders,menuSnapshotApplies,motionApplied,thread:Process.getCurrentThreadId(),nativeUpdateThread});
      }else if([1,3].includes(testNativeMenuStage) && Date.now()-testNativeMenuAt>=2000){
        const list=testNativeModal.add(0x10).readPointer();
        const current=list.add(8).readPointer().add(list.add(0x70).readS32()*8).readPointer();
        if(!current.equals(testNativeModal))throw new Error('Native menu test lost active modal');
        new NativeFunction(game.base.add(0x118070),'void',['pointer','pointer','float'])(list,testNativeModal,0.5);
        send({type:'native-menu-test',action:'closed',tab:testNativeMenuStage===1?1:0x10,displayFrames,nativeMapRenders,menuSnapshotApplies,motionApplied,thread:Process.getCurrentThreadId(),nativeUpdateThread});
        testNativeModal=null;testNativeMenuStage++;
      }
    }
    const active=topState(state);if(active!==nativeFlightActive){nativeFlightActive=active;if(lastNativeIntent)sendNativeIntent(lastNativeIntent);}
    if(campaignReplicaZone && nativeUpdateThread!==null){
      if(Process.getCurrentThreadId()!==nativeUpdateThread)throw new Error('Native UI update thread differs from replica update thread');
      consumeMotion(campaignReplicaZone,'ui');
      if(!active){applyingFromMenu=true;try{sample(campaignReplicaZone);}finally{applyingFromMenu=false;}}
    }
    }finally{heartbeatBusy=false;}
  };
  Interceptor.attach(game.base.add(0x11c700),{onEnter(args){nativeFlightState=args[0];}});
  Interceptor.attach(game.base.add(0x118300),{onLeave(){nativeUIHeartbeat();}});
}
if(cfg.renderOnly) {
  // These hooks observe the native editor lifecycle. They never open a menu.
  Interceptor.attach(game.base.add(0x9d690),{onEnter(args){this.editor=args[0].toString();},onLeave(){openEditors.add(this.editor);pilotKeys.clear();mouseHeld=false;}});
  Interceptor.attach(game.base.add(0x9e080),{onEnter(args){openEditors.delete(args[0].toString());pilotKeys.clear();mouseHeld=false;}});
  let sdlHooksInstalled=false;
  // SDL initializes its dynamic API during startup. Install its hooks after
  // initialization so rewritten entry stubs cannot discard the hooks.
  Interceptor.attach(game.getExportByName('?DrawGame@GameZone@@QEAAXAEBUView@@@Z'),{onEnter(args){
    if(cfg.nativeCampaignClient && campaignReplicaZone && !args[0].equals(campaignReplicaZone))return;
    this.motionTraceZone=args[0];this.motionTraceView=args[1];
    nativeDrawThread=Process.getCurrentThreadId();
    drawCalls++;
    // Apply frequent state at a render boundary as well when native flight
    // and rendering run on the same verified game thread.
    if(cfg.fastMotion && nativeUpdateThread===Process.getCurrentThreadId())consumeMotion(args[0]);
    if(visualTick && visualTick(args[0])<0) throw new Error('Native visual effect replay failed');
    presentReplica(args[0],'draw');
    if(cfg.followPilot && replicaPilot && !cfg.nativeCampaignClient && !comparisonViewConfig) {
      // View offsets are checked against DrawGame's current machine code and
      // live viewport values; this is the replica camera, never host state.
      args[1].add(0x10).writeFloat(replicaPilot[0]);
      args[1].add(0x14).writeFloat(replicaPilot[1]);
      args[1].add(0x20).writeFloat(2);
    }
    if(drawCalls%60===1) lastView=Array.from({length:18},(_,i)=>args[1].add(i*4).readFloat());
    if(comparisonViewConfig){args[1]=applyComparisonView(args[0],args[1],this);this.motionTraceView=args[1];}
    if(sdlHooksInstalled) return;
    sdlHooksInstalled=true;
    const sdl=Process.getModuleByName('SDL2.dll');
    getWindow=new NativeFunction(sdl.getExportByName('SDL_GetWindowFromID'),'pointer',['uint']);
    getWindowSize=new NativeFunction(sdl.getExportByName('SDL_GetWindowSize'),'void',['pointer','pointer','pointer']);
    textInputActive=new NativeFunction(sdl.getExportByName('SDL_IsTextInputActive'),'int',[]);
    const pace=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPaceFrame'),'void',[],{scheduling:'cooperative'});
    let titleSet=false;
    const setTitle=new NativeFunction(sdl.getExportByName('SDL_SetWindowTitle'),'void',['pointer','pointer']);
    const sessionTitle=Memory.allocUtf8String(cfg.windowTitle || (cfg.nativeCampaignClient?'Reassembly — Repopulated Client':'Reassembly — Repopulated Host'));
    Interceptor.attach(sdl.getExportByName('SDL_GL_SwapWindow'),{onEnter(args){
      nativeSwapThread=Process.getCurrentThreadId();
      if(!titleSet){setTitle(args[0],sessionTitle);titleSet=true;}
      if(cfg.frameLimit)pace();
      const now=Date.now();displayFrames++;
      if(lastPresentation) {const ms=now-lastPresentation;frameHistogram[Math.min(250,ms)]++;if(ms>50)longFrames++;maxFrameMs=Math.max(maxFrameMs,ms);}
      lastPresentation=now;
    }});
    if(cfg.pilotInput) Interceptor.attach(sdl.getExportByName('SDL_PollEvent'),{
    onEnter(args){this.event=args[0];pollCalls++;},
    onLeave(result){
      if(result.toInt32()!==1 || this.event.isNull()) return;
      const type=this.event.readU32();
      if(cfg.nativeCampaignClient){
        if(type===0x200){
          const event=this.event.add(12).readU8();
          if(event===12)nativeFocused=true;
          else if(event===13 || event===7){nativeFocused=false;if(lastNativeIntent)sendNativeIntent(lastNativeIntent);}
        }
        return;
      }
      if(openEditors.size) {pilotKeys.clear();mouseHeld=false;return;}
      if(type===0x200 && this.event.add(12).readU8()===13) {pilotKeys.clear();mouseHeld=false;mouseOffset=null;}
      if(type===0x400 || type===0x401 || type===0x402) {
        mouseWindow=getWindow(this.event.add(8).readU32());
        const offset=type===0x400?16:20;
        mouseX=this.event.add(offset).readS32();mouseY=this.event.add(offset+4).readS32();
        if(type!==0x400 && this.event.add(16).readU8()===1) {mouseHeld=type===0x401;this.event.writeU32(0);}
        return;
      }
      if(type!==0x300 && type!==0x301) return;
      const key=this.event.add(20).readS32();
      if(![119,97,115,100].includes(key)) return;
      if(type===0x300) pilotKeys.add(key);else pilotKeys.delete(key);
      this.event.writeU32(0); // Consume only multiplayer flight keys.
    }
    });
    send({type:'sdl-input-ready'});
  },onLeave(){if(this.motionTraceZone)tracePresentation(this.motionTraceZone,this.motionTraceView,this.comparisonView || null);}});
}
if(!cfg.renderOnly && (cfg.measureRendering || cfg.windowTitle || comparisonViewConfig)){
  let hostSdlHooksInstalled=false;
  Interceptor.attach(game.getExportByName('?DrawGame@GameZone@@QEAAXAEBUView@@@Z'),{onEnter(args){
    this.motionTraceZone=args[0];this.motionTraceView=args[1];drawCalls++;
    nativeDrawThread=Process.getCurrentThreadId();
    capturePresentationStage('draw');
    if(comparisonViewConfig){args[1]=applyComparisonView(args[0],args[1],this);this.motionTraceView=args[1];}
    if(hostSdlHooksInstalled)return;hostSdlHooksInstalled=true;
    const sdl=Process.getModuleByName('SDL2.dll');
    const setTitle=new NativeFunction(sdl.getExportByName('SDL_SetWindowTitle'),'void',['pointer','pointer']);
    const comparisonPace=comparisonFrameLimit?new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPaceFrame'),'void',[]):null;
    const title=Memory.allocUtf8String(cfg.windowTitle || 'Reassembly — Repopulated Host');let titleSet=false;
    Interceptor.attach(sdl.getExportByName('SDL_GL_SwapWindow'),{onEnter(args){
      nativeSwapThread=Process.getCurrentThreadId();
      if(!titleSet){setTitle(args[0],title);titleSet=true;}
      if(comparisonPace)comparisonPace();
      const now=Date.now();displayFrames++;
      if(lastPresentation){const ms=now-lastPresentation;frameHistogram[Math.min(250,ms)]++;if(ms>50)longFrames++;maxFrameMs=Math.max(maxFrameMs,ms);}
      lastPresentation=now;
    }});
  },onLeave(){if(this.motionTraceZone)tracePresentation(this.motionTraceZone,this.motionTraceView,this.comparisonView || null);}});
}
let damageFixture=null,damagedFixture=false;
let partialDamageFixture=null,partiallyDamagedFixture=false;
let healthReader=null;
let driver=null,aimDriver=null,lastRemoteRespawn=0,remoteDestroyed=false;
let nativeDriver=null,nativeWeapons=null;
const nativeWeaponPulses=new Map();
let remoteControlEnabled=!cfg.vacantRemoteAI;
let vacantAICalls=0;
let campaignSaveProbed=false;
let pendingCheckpoint=null;
function driveCommand(zone,state,stale) {
  if(state.action==='native')return nativeDriver(zone,state.ownerFaction,cfg.activeShips[state.ownerFaction],state.dimensions,
    state.nativeDestination,state.nativePrecision,Number(stale));
  const args=[zone,state.ownerFaction,state.targetFaction,(cfg.activeShips || {})[state.ownerFaction] || 0,stale?0:state.x,stale?0:state.y];
  return state.angle!==undefined && aimDriver ? aimDriver(...args,state.angle) : driver(...args);
}
function fireCommand(zone,state) {
  return (cfg.activeShips || {})[state.ownerFaction]
    ?fireShip(zone,state.ownerFaction,state.targetFaction,cfg.activeShips[state.ownerFaction],state.x,state.y)
    :fire(zone,state.ownerFaction,state.targetFaction,state.x,state.y);
}
const driveStates=new Map();
const fireStates=new Map();
function runtimeHealth(zone) {
    const begin=zone.add(0x188).readPointer(),end=zone.add(0x190).readPointer();
    const roots=end.sub(begin).toInt32()/8;
    if(roots<0 || roots>4096 || !Number.isInteger(roots)) throw new Error('Invalid health scene');
    const buffer=Memory.alloc(4096*4),entities=[];
    let total=0;
    for(let i=0;i<roots;i++) {
      const cluster=begin.add(i*8).readPointer();
      if(!cluster.add(0x178).readPointer().isNull()) continue;
      const ident=clusterIdent(cluster),count=healthReader(zone,ident,buffer,4096);
      if(count<1 || (total+=count)>65536) throw new Error('Native health read failed: '+count);
      const values=[];
      for(let j=0;j<count;j++) values.push(buffer.add(j*4).readFloat());
      values.sort((a,b)=>a-b);entities.push({ident,values});
    }
    return entities;
}
function ensureSceneIdentities(zone) {
  const begin=zone.add(0x188).readPointer(),end=zone.add(0x190).readPointer();
  const count=end.sub(begin).toInt32()/8;
  if(!Number.isInteger(count) || count<0 || count>4096) throw new Error('Invalid identity scene');
  for(let i=0;i<count;i++) {const ident=clusterIdent(begin.add(i*8).readPointer());if(ident)knownIdents.add(ident);}
  for(let i=0;i<count;i++) {
    const cluster=begin.add(i*8).readPointer();if(clusterIdent(cluster))continue;
    while(knownIdents.has(nextIdent))nextIdent++;
    const assigned=ensureIdent(cluster,nextIdent);
    if(assigned){knownIdents.add(assigned);nextIdent++;}
  }
}
function playerHealth(zone) {
  const read=healthReader || new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedReadHealth'),'int',['pointer','uint','pointer','int']);
  const buffer=Memory.alloc(4096*4);
  return [0x70000001,0x70000002].map(ident=>{
    const count=read(zone,ident,buffer,4096);
    if(count<1) throw new Error('Player health snapshot unavailable: '+count);
    const values=Array.from({length:count},(_,i)=>buffer.add(i*4).readFloat()).sort((a,b)=>a-b);
    return {ident,values};
  });
}
function pilotHealth(zone) {
  const buffer=Memory.alloc(4096*4),count=healthReader(zone,0x70000002,buffer,4096);
  if(count<1)throw new Error('Pilot health unavailable: '+count);
  return [{ident:0x70000002,values:Array.from({length:count},(_,i)=>buffer.add(i*4).readFloat()).sort((a,b)=>a-b)}];
}
rpc.exports = { applymotion(frame){
  if(!cfg.fastMotion || !Number.isInteger(frame.seq) || frame.seq<=motionSequence || typeof frame.poses!=='string' || !frame.poses.length || frame.poses.length%88 || frame.poses.length>4096*88 || !/^[a-f0-9]+$/.test(frame.poses))throw new Error('Invalid native motion frame');
  const started=Date.now();
  if(lastMotionPrepared)timing('prepareInterval',started-lastMotionPrepared);lastMotionPrepared=started;
  const received=Number.isFinite(frame.receivedAt)?Math.min(started,frame.receivedAt):started;
  timing('receiveToPrepare',started-received);
  if(!Number.isSafeInteger(frame.sourceTimeMs) || frame.sourceTimeMs<=0 || frame.sourceTimeMs>1e12)throw new Error('Invalid native motion source time');
  if(!Number.isSafeInteger(frame.simTimeMs) || frame.simTimeMs<0 || frame.simTimeMs>1e12)throw new Error('Invalid native simulation time');
  const prepared={seq:frame.seq,buffer:packedBuffer(frame.poses),count:frame.poses.length/88,received,inputTick:frame.inputTick,sourceTimeMs:frame.sourceTimeMs,simTimeMs:frame.simTimeMs};
  for(const [key,size,limit] of [['blocks',56,4096],['projectiles',36,2048],['movers',16,4096],['health',16,65536]]){
    const hex=frame[key];if(typeof hex!=='string' || hex.length%(size*2) || hex.length>limit*size*2 || hex.length && !/^[a-f0-9]+$/.test(hex))throw new Error('Invalid realtime '+key);
    prepared[key]={buffer:packedBuffer(hex),count:hex.length/(size*2)};
  }
  motionSequence=frame.seq;queueMotionFrame(prepared);retainVisualFrame(preparedVisualFrames,prepared);
  timing('motionPrepare',Date.now()-started);return true;
}, enqueue(command) {
  if (!cfg.networkControl) throw new Error('Network controls unavailable');
  if(command.action==='native')for(const row of command.weapons){
    if(row[1] && (nativeWeaponPulses.has(row[0]) || nativeWeaponPulses.size<512))nativeWeaponPulses.set(row[0],{mask:(nativeWeaponPulses.get(row[0])?.mask || 0)|row[1],received:Date.now()});
  }
  // Start the test pause after real input has flowed, rather than during loading.
  if(cfg.holdControlsMs && !controlHoldStarted && ++receivedControls>=20){
    controlHoldStarted=true;holdControlsUntil=Date.now()+cfg.holdControlsMs;
  }
  // Continuous controls describe the latest state. Replaying a backlog after
  // a paused update loop causes lag and eventually exhausted the old queue.
  if(cfg.directControl && ['drive','fire','native'].includes(command.action)) {
    const index=pendingMoves.findIndex(c=>c.ownerFaction===command.ownerFaction && c.action===command.action);
    if(index>=0) {pendingMoves[index]={...command,queuedAt:Date.now()};coalescedControls++;return true;}
  }
  if(pendingMoves.length>=32) {droppedControls++;return false;}
  pendingMoves.push({...command,queuedAt:Date.now()}); return true;
}, controlstats() {return {pending:pendingMoves.length,coalesced:coalescedControls,dropped:droppedControls,peak:peakPendingControls,playerControlled:remoteControlEnabled,vacantAICalls,
  testHoldStarted:controlHoldStarted,testHoldCompleted:controlHoldStarted && Date.now()>=holdControlsUntil,hostAI:hostAIStats,nativeAIInstalled:hostAIOwnedCallback!==null};}, setremotecontrol(enabled) {
  if(!cfg.vacantRemoteAI || typeof enabled!=='boolean') throw new Error('Remote ownership unavailable');
  remoteControlEnabled=enabled;pendingMoves.length=0;driveStates.clear();fireStates.clear();nativeWeaponPulses.clear();
  send({type:'remote-ownership',playerControlled:enabled});return true;
}, checkpoint(request) {
  if(!cfg.campaignCheckpoints || pendingCheckpoint || typeof request.id!=='string' || !/^[a-f0-9]{32}$/.test(request.id) ||
     typeof request.path!=='string' || request.path.includes('..') ||
     request.path.replaceAll('\\','/')!==cfg.sandbox.replaceAll('\\','/')+'/checkpoint-'+request.id) throw new Error('Invalid private checkpoint request');
  pendingCheckpoint=request;return true;
}, applyworld(world) {
  const prepareStarted=Date.now();
  if(!cfg.worldReplica || !Number.isSafeInteger(world.seq) || world.seq<=worldSeq ||
     typeof world.path!=='string' || world.path.includes('..') ||
     !world.path.replaceAll('\\','/').startsWith(cfg.sandbox.replaceAll('\\','/')+'/')) throw new Error('Invalid world update');
  if(cfg.persistentReplica){
    const plan=world.incremental;
    if(!plan || typeof world.hasAdditions!=='boolean' || !Array.isArray(plan.remove) || plan.remove.length>4096 ||
       new Set(plan.remove).size!==plan.remove.length || plan.remove.some(id=>!Number.isInteger(id)||id<=0||id>0xffffffff))throw new Error('Invalid incremental removals');
    replicaContinuity(plan);
    for(const [key,size,limit] of [['poses',40,4096],['health',16,65536]]){
      const hex=plan[key];if(typeof hex!=='string'||!hex.length||hex.length%(size*2)||hex.length>size*limit*2||!/^[a-f0-9]+$/.test(hex))throw new Error('Invalid incremental state');
    }
  }
  if(cfg.syncCampaignMap){
    const map=world.map;
    if(!map || !Number.isInteger(map.width) || map.width<1 || map.width>256 ||
       typeof map.radius!=='string' || !/^[a-f0-9]{16}$/.test(map.radius) ||
       typeof map.cells!=='string' || map.cells.length!==map.width*map.width*24 || !/^[a-f0-9]+$/.test(map.cells) ||
       typeof map.regions!=='string' || map.regions.length%24 || map.regions.length>1024*24 || !/^[a-f0-9]*$/.test(map.regions) ||
       typeof map.objectives!=='string' || map.objectives.length%72 || map.objectives.length>8192*72 || !/^[a-f0-9]*$/.test(map.objectives))throw new Error('Invalid packed campaign map');
  }
  if(cfg.replicatePresentation) {
    const sizes={blocks:[56,4096],projectiles:[36,2048],thrust:[44,512],motion:[8,4096]};
    if(!world.visuals || Object.keys(world.visuals).sort().join(',')!=='blocks,motion,projectiles,thrust') throw new Error('Missing presentation state');
    for(const key of Object.keys(sizes)) {
      const value=world.visuals[key],size=sizes[key][0];
      if(typeof value!=='string' || value.length%(size*2) || value.length>size*sizes[key][1]*2 || !/^[a-f0-9]*$/.test(value))throw new Error('Invalid packed presentation state');
    }
  }
  if(cfg.persistentReplica){
    const plan=world.incremental;
    world.preparedIncremental={poses:{buffer:packedBuffer(plan.poses),count:plan.poses.length/80},
                               health:{buffer:packedBuffer(plan.health),count:plan.health.length/32}};
  }
  worldSeq=world.seq; pendingWorld=world; timing('worldPrepare',Date.now()-prepareStarted);return true;
}, applystate(state) {
  if (!cfg.replica || !Number.isSafeInteger(state.seq) || state.seq <= stateSeq ||
      !Array.isArray(state.ships) || !state.ships.length || state.ships.length>4096) throw new Error('Invalid replica state');
  const factions = cfg.factions || [7,8];
  if (new Set(state.ships.map(s=>s.ident)).size !== state.ships.length) throw new Error('Ambiguous replica identity');
  for (const ship of state.ships) {
    if (!factions.includes(ship.faction)) throw new Error('Unknown replica identity');
    if (!Number.isInteger(ship.ident) || ship.ident<=0 || ship.ident>0xffffffff) throw new Error('Invalid entity identity');
    for (const key of ['x','y','vx','vy','angle']) {
      const limit = key === 'x' || key === 'y' ? 1000000 : 10000;
      if (typeof ship[key] !== 'number' || !Number.isFinite(ship[key]) || Math.abs(ship[key])>limit)
        throw new Error('Invalid replica coordinate');
    }
  }
  stateSeq = state.seq; pendingState = state; return true;
}};
function receiveStream(){
  recv('repopulated-stream',message=>{
    try{
      const {kind,value}=message.payload;
      streamStats[kind]=(streamStats[kind] || 0)+1;const started=Date.now();
      if(kind==='input')rpc.exports.enqueue(value);
      else if(kind==='motion')rpc.exports.applymotion(value);
      else if(kind==='world')rpc.exports.applyworld(value);
      else throw new Error('Unknown native stream message');
      streamStats.maxPrepareMs=Math.max(streamStats.maxPrepareMs,Date.now()-started);
    }catch(error){send({type:'error',description:String(error),stack:error.stack});}
    receiveStream();
  });
}
receiveStream();
const kernel = Process.getModuleByName('KERNEL32.dll');
let monotonicClock=null;
if(cfg.worldReplica && !cfg.nativeCampaignClient) Interceptor.attach(game.base.add(0xcab60), {
  onEnter(args) { consoleContext=args[0]; }
});
if (cfg.traceFiles) for (const name of ['CreateFileW','CreateFileA','GetFileAttributesW','GetFileAttributesA']) {
  Interceptor.attach(Process.getModuleByName('KERNELBASE.dll').getExportByName(name), {
    onEnter(args) {
      const path = name.endsWith('W') ? args[0].readUtf16String() : args[0].readCString();
      if (path && (path.includes('checkpoint') || cfg.trackPlayer && path.includes('save0')))
        send({type:'checkpoint-file-access', path});
    }
  });
}
const load = new NativeFunction(kernel.getExportByName('LoadLibraryW'), 'pointer', ['pointer']);
function dispatchControls(zone) {
    peakPendingControls=Math.max(peakPendingControls,pendingMoves.length);
    if(Date.now()<holdControlsUntil) return;
    if(!mover) return;
    while(pendingMoves.length) {
      const command=pendingMoves.shift();
      if(command.action==='drive') {
        if(!driver) throw new Error('Direct controls unavailable');
        const stale=Date.now()-command.queuedAt>500;
        driveStates.set(command.ownerFaction,{...command,received:command.queuedAt});
        send({type:'network-drive-result',...command,result:driveCommand(zone,command,stale)});
      } else if(command.action==='native'){
        command.nativeDestination=Memory.alloc(24);command.destination.forEach((v,i)=>command.nativeDestination.add(i*4).writeFloat(v));
        command.nativePrecision=Memory.alloc(16);command.precision.forEach((v,i)=>command.nativePrecision.add(i*4).writeFloat(v));
        command.nativeWeaponBuffer=Memory.alloc(Math.max(1,command.weapons.length*32));
        command.weapons.forEach((row,i)=>row.forEach((v,j)=>{const cell=command.nativeWeaponBuffer.add(i*32+j*4);if(j<2)cell.writeU32(v);else cell.writeFloat(v);}));
        latestInputSequence=command.seq;
        latestInputTick=command.clientTick;
        if(typeof command.viewRadius==='number'){
          if(command.viewRadius>=clientViewRadius || Date.now()-clientViewChangedAt>2000){clientViewRadius=command.viewRadius;clientViewChangedAt=Date.now();}
        }
        driveStates.set(command.ownerFaction,{...command,received:command.queuedAt});
        fireStates.set(command.ownerFaction,{...command,received:command.queuedAt,report:true});
        send({type:'network-drive-result',seq:command.seq,result:driveCommand(zone,command,Date.now()-command.queuedAt>500)});
      } else if(command.action==='fire') {
        if(cfg.directControl && typeof command.held==='boolean')
          fireStates.set(command.ownerFaction,{...command,received:command.queuedAt,report:true});
        else send({type:'network-fire-result',...command,result:cfg.directControl && Date.now()-command.queuedAt>500 ? 0 :fireCommand(zone,command)});
      }
      else send({type:'network-waypoint-result',...command,
        result:mover(zone,command.ownerFaction,command.targetFaction,command.x,command.y)});
    }
}
function sample(zone) {
    if(campaignAuthorityEnded)return;
    if(cfg.nativeCampaignClient && (!campaignReplicaZone || !zone.equals(campaignReplicaZone)))return;
    if(cfg.campaignRemote && campaignAuthorityZone && !zone.equals(campaignAuthorityZone))return;
    callbacks++;
    if (sampled >= (cfg.maxSamples || 256) || Date.now()-lastSample<(cfg.sampleIntervalMs || 1000)) return;
    if(cfg.campaignSaveProbe && !campaignSaveProbed && sampled>=10) {
      campaignSaveProbed=true;
      const save=game.base.add(0x3cf700).readPointer();
      if(save.isNull() || !redirects) throw new Error('Private save context unavailable');
      const functions=[{rva:0x1d9e20,prefix:[0x48,0x89,0x5c,0x24,0x18,0x55,0x56,0x57]},
                       {rva:0x1d9a40,prefix:[0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x74,0x24,0x18]}];
      const streamer=new NativeFunction(game.getExportByName('?getStreamer@GameZone@@QEAAPEAUStreamerBase@@XZ'),'pointer',['pointer'])(zone);
      if(streamer.isNull()) throw new Error('Campaign streamer unavailable');
      const vtable=streamer.readPointer(),saveLoaded=vtable.add(0x20).readPointer(),flush=vtable.add(0x28).readPointer();
      if(!saveLoaded.equals(game.base.add(0x222d30)) || !flush.equals(game.base.add(0x222570)))
        throw new Error('Unverified campaign streamer type');
      const sectorsSaved=new NativeFunction(saveLoaded,'bool',['pointer'])(streamer);
      new NativeFunction(flush,'void',['pointer'])(streamer);
      const results=functions.map(f=>{
        const actual=new Uint8Array(game.base.add(f.rva).readByteArray(f.prefix.length));
        if(!f.prefix.every((b,i)=>actual[i]===b)) throw new Error('Save code signature differs');
        return new NativeFunction(game.base.add(f.rva),'bool',['pointer'])(save);
      });
      const virtuals=streamer.isNull()?[]:Array.from({length:12},(_,i)=>streamer.readPointer().add(i*8).readPointer().sub(game.base).toString());
      send({type:'campaign-native-save',save:results[0],blueprints:results[1],sectorsSaved,streamerVirtuals:virtuals});
    }
    lastSample = Date.now();
    if (!loaded) {
      if (!redirects) { send({type:'isolation-not-confirmed'}); return; }
      const dll = load(Memory.allocUtf16String(cfg.dll));
      if (dll.isNull()) throw new Error('Diagnostic DLL load failed');
      sampler = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName(cfg.diagnosticSamples===false?'RepopulatedCountNativeClusters':'RepopulatedSample'), 'int', ['pointer']);
      installWireHex(Process.getModuleByName('RepopulatedDiagnostic.dll'));
      const ownershipExport=Process.getModuleByName('RepopulatedDiagnostic.dll').findExportByName('RepopulatedOwnershipStats');
      if(ownershipExport){ownershipStatsReader=new NativeFunction(ownershipExport,'void',['pointer']);ownershipStatsBuffer=Memory.alloc(32);}
      clusterIdent = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedClusterIdent'),'uint',['pointer']);
      ensureIdent = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedEnsureClusterIdent'),'uint',['pointer','uint']);
      if(cfg.worldStream || cfg.worldReplica) worldExporter=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedExportWorld'),'int',['pointer','pointer']);
      if(cfg.interestIdent) interestExporter=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedExportInterest'),'int',['pointer','pointer','uint','float']);
      if(cfg.worldStream || cfg.worldReplica) healthReader=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedReadHealth'),'int',['pointer','uint','pointer','int']);
      if(cfg.worldReplica) worldLoader=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName(cfg.persistentReplica?'RepopulatedAppendWorldVisual':cfg.renderOnly?'RepopulatedLoadWorldVisual':'RepopulatedLoadWorld'),'int',['pointer','pointer','pointer']);
      if(cfg.persistentReplica){
        if(!replicaInitialized){
          new NativeFunction(game.getExportByName('?Clear@GameZone@@QEAAX_N@Z'),'void',['pointer','bool'])(zone,0);
          replicaInitialized=true;
        }
        const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
        replicaUpdater=new NativeFunction(dll.getExportByName('RepopulatedUpdateReplica'),'int',['pointer','pointer','int','pointer','int']);
        replicaRemover=new NativeFunction(dll.getExportByName('RepopulatedRemoveReplica'),'int',['pointer','uint']);
        replicaReplacementRemover=new NativeFunction(dll.getExportByName('RepopulatedRemoveReplicaForReplacement'),'int',['pointer','uint','uint','int']);
        const fragmentExport=dll.findExportByName('RepopulatedRemoveReplicaForFragmentReplacement');
        if(fragmentExport)replicaFragmentReplacementRemover=new NativeFunction(fragmentExport,'int',['pointer','uint','uint']);
        const replacementStatsExport=dll.findExportByName('RepopulatedReplacementStats');
        if(replacementStatsExport){replacementStatsReader=new NativeFunction(replacementStatsExport,'void',['pointer']);replacementStatsBuffer=Memory.alloc(64);}
        if(cfg.nativeCampaignClient){
          replicaBatchRemover=new NativeFunction(dll.getExportByName('RepopulatedRemoveReplicaBatch'),'int',['pointer','pointer','int']);
          const batchMessageExport=dll.findExportByName('RepopulatedRemoveReplicaBatchMessage');
          if(batchMessageExport)replicaBatchMessage=new NativeFunction(batchMessageExport,'pointer',[]);
        }
        replicaStats=new NativeFunction(dll.getExportByName('RepopulatedReplicaStats'),'void',['pointer']);
      }
      if(cfg.nativeCampaignClient)installReplicaField(Process.getModuleByName('RepopulatedDiagnostic.dll'),zone);
      if(cfg.worldReplica) worldLoadMessage=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedWorldLoadMessage'),'pointer',[]);
      if(cfg.syncCampaignMap){
        const module=Process.getModuleByName('RepopulatedDiagnostic.dll');
        campaignMapExporter=new NativeFunction(module.getExportByName('RepopulatedExportCampaignMap'),'int',['pointer','pointer']);
        campaignMapRemoteExporter=new NativeFunction(module.getExportByName('RepopulatedExportRemoteCampaignMap'),'int',['pointer','pointer']);
        campaignMapApply=new NativeFunction(module.getExportByName('RepopulatedApplyCampaignMap'),'int',['pointer','pointer','int','pointer','int','pointer','int']);
        campaignMapExplore=new NativeFunction(module.getExportByName('RepopulatedExploreRemoteMap'),'int',['pointer']);
        campaignObjectivesApply=new NativeFunction(module.getExportByName('RepopulatedApplyCampaignObjectives'),'int',['pointer','pointer','int']);
        if(cfg.campaignRemote){
          const notify=game.base.add(0x1cac40),prefix=[0x48,0x89,0x5c,0x24,0x10,0x48,0x89,0x6c,0x24,0x18];
          const actual=new Uint8Array(notify.readByteArray(prefix.length));
          if(prefix.some((v,i)=>actual[i]!==v))throw new Error('Native exploration notifier signature differs');
          const original=Interceptor.replaceFast(notify,module.getExportByName('RepopulatedCampaignMapNotify'));
          new NativeFunction(module.getExportByName('RepopulatedSetMapNotifyOriginal'),'void',['pointer'])(original);
        }
      }
      if(cfg.streamPresentation) {
        const module=Process.getModuleByName('RepopulatedDiagnostic.dll');
        visualExporter=new NativeFunction(module.getExportByName('RepopulatedExportPresentation'),'int',['pointer','pointer','uint','float']);
        visualIdentities=new NativeFunction(module.getExportByName('RepopulatedEnsureVisualIdentities'),'int',['pointer']);
      }
      if(thrustAuditIdent && !cfg.renderOnly)installHostThrustAudit(Process.getModuleByName('RepopulatedDiagnostic.dll'));
      if(cfg.streamPresentation && !cfg.streamMotion || thrustAuditIdent && !cfg.renderOnly){
        const module=Process.getModuleByName('RepopulatedDiagnostic.dll');
        const effect=game.base.add(0x1cc440),prefix=[0x48,0x8b,0xc4,0x48,0x89,0x58,0x08,0x55,0x48,0x8d,0x68,0xc1];
        const actual=new Uint8Array(effect.readByteArray(prefix.length));
        if(prefix.some((b,i)=>actual[i]!==b)) throw new Error('Native thrust signature differs');
        const original=Interceptor.replaceFast(effect,module.getExportByName('RepopulatedCaptureThrust'));
        try{new NativeFunction(module.getExportByName('RepopulatedSetThrustOriginal'),'void',['pointer'])(original);}
        catch(error){Interceptor.revert(effect);throw error;}
      }
      if(cfg.damageTest) damageFixture=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedDamageFixture'),'int',['pointer','int']);
      if(cfg.damageTest || cfg.verifyPilotHealth) partialDamageFixture=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPartialDamageFixture'),'int',['pointer','int']);
      if (cfg.moves || cfg.networkControl) mover = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedMoveOwned'), 'int', ['pointer','int','int','float','float']);
      if(cfg.directControl) driver=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedDriveOwned'),'int',['pointer','int','int','uint','float','float']);
      if(cfg.directControl) aimDriver=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedDriveAimed'),'int',['pointer','int','int','uint','float','float','float']);
      if(cfg.directControl){
        const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
        nativeDriver=new NativeFunction(dll.getExportByName('RepopulatedDriveNative'),'int',['pointer','int','uint','uint','pointer','pointer','bool']);
        nativeWeapons=new NativeFunction(dll.getExportByName('RepopulatedNativeWeapons'),'int',['pointer','int','uint','pointer','int','bool']);
      }
      if (cfg.replica) poseSetter = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedApplyPose'), 'int', ['pointer','int','uint','float','float','float','float','float']);
      if (cfg.fireTest || cfg.networkControl) fire = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedFireOwned'), 'int', ['pointer','int','int','float','float']);
      if(cfg.activeShips) fireShip=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedFireShip'),'int',['pointer','int','int','uint','float','float']);
      if(cfg.activeShips) releaseWeapons=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedReleaseWeapons'),'int',['pointer','int','uint']);
      if (cfg.exportCluster || cfg.exportFactions) exporter = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedExportCluster'), 'int', ['pointer','int']);
      loaded = true; send({type:'sampler-loaded',diagnosticSamples:cfg.diagnosticSamples!==false});
    }
    const result = sampler(zone); sampled++;
    if(cfg.replica && cfg.initialIdentities && !initialBindingsApplied && result>=cfg.initialIdentities.length) {
      const begin=zone.add(0x188).readPointer();
      const restoredZeroIds=[];
      for(const identity of cfg.initialIdentities) {
        const matches=[];
        for(let i=0;i<Math.min(result,4096);i++) {
          const cluster=begin.add(i*8).readPointer();
          if(cluster.add(0x118).readS32()===identity.faction && clusterIdent(cluster)===identity.ident) matches.push(cluster);
        }
        if(matches.length!==1) throw new Error('Ambiguous initial entity binding');
        const current=clusterIdent(matches[0]);
        if(current && current!==identity.ident) throw new Error('Initial entity identity changed');
        if(!current) throw new Error('Initial identity not preserved by level reconstruction');
        if(!current) restoredZeroIds.push(identity.ident);
      }
      initialBindingsApplied=true; send({type:'initial-identities-bound',identities:cfg.initialIdentities,restoredZeroIds});
    }
    if (cfg.streamState && result>=1) {
      ensureSceneIdentities(zone);
    }
    if (exporter && !exported && result>=1) {
      exported=true;
      if (cfg.exportFactions) {
        const setEnv = new NativeFunction(kernel.getExportByName('SetEnvironmentVariableW'),'int',['pointer','pointer']);
        const files=[];
        for (const faction of cfg.exportFactions) {
          const path=cfg.sandbox+'/faction-'+faction+'.lua';
          if (!setEnv(Memory.allocUtf16String('REPOPULATED_CLUSTER_EXPORT'),Memory.allocUtf16String(path)))
            throw new Error('Bootstrap export path failed');
          files.push({faction,path,bytes:exporter(zone,faction)});
        }
        send({type:'bootstrap-exported',files});
      } else send({type:'cluster-export-result',bytes:exporter(zone,8)});
    }
    if (fire && cfg.fireTest && result >= 1) {
      const begin=zone.add(0x188).readPointer(), end=zone.add(0x190).readPointer();
      const count=Math.min(4096,end.sub(begin).toInt32()/8);
      for (let i=0;i<count;i++) {
        const cluster=begin.add(i*8).readPointer();
        if (cluster.add(0x118).readS32()!==8) continue;
        const x=cluster.add(0x30).readDouble()+500, y=cluster.add(0x38).readDouble();
        send({type:'weapon-fire-result',result:fire(zone,8,8,x,y)}); break;
      }
    }
    if(worldExporter && cfg.worldStream) {
      const sceneExportStarted=Date.now();
      if(visualIdentities && visualIdentities(zone)<0)throw new Error('Native visual identity assignment failed');
      const path=cfg.sandbox+'/world-'+(cfg.rollingWorld?sampled%16:sampled)+'.lua';
      const radius=cfg.dynamicInterest?Math.min(20000,clientViewRadius+1500):cfg.interestRadius || 3000;
      let exportStage=Date.now();
      let roots=interestExporter?interestExporter(zone,Memory.allocUtf8String(path),cfg.interestIdent,radius):worldExporter(zone,Memory.allocUtf8String(path));
      timing('worldExport',Date.now()-exportStage);
      if(roots===-10 && cfg.remoteRespawn && Date.now()-lastRemoteRespawn>2000) {
        lastRemoteRespawn=Date.now();
        const save=game.base.add(0x3cf700).readPointer(),player=game.base.add(0x3cf930).readPointer();
        let x=500,y=0;
        if(!player.isNull()) {
          const command=player.add(0xa8).readPointer();
          if(!command.isNull()) {
            const cluster=command.add(0xb8).readPointer();
            if(!cluster.isNull()) {x=cluster.add(0x30).readDouble()+500;y=cluster.add(0x38).readDouble();}
          }
        }
        const spawnRemote=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedSpawnRemoteFixture'),
          'int',['pointer','pointer','float','float']);
        const spawned=spawnRemote(zone,save.add(0x20).readPointer(),x,y);
        send({type:'remote-respawn',result:spawned,x,y});
        if(spawned!==1) throw new Error('Remote respawn failed');
        driveStates.clear();fireStates.clear();
        ensureSceneIdentities(zone);
        if(visualIdentities && visualIdentities(zone)<0)throw new Error('Respawn visual identity assignment failed');
        roots=interestExporter(zone,Memory.allocUtf8String(path),cfg.interestIdent,radius);
      }
      let visualPath=null;
      if(visualExporter && roots>=0) {
        exportStage=Date.now();
        visualPath=cfg.sandbox+'/visual-'+(cfg.rollingWorld?sampled%16:sampled)+'.json';
        const visuals=visualExporter(zone,Memory.allocUtf8String(visualPath),cfg.interestIdent,radius);
        if(visuals<0) {
          const message=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPresentationMessage'),'pointer',[])().readUtf8String();
          throw new Error('Native presentation export failed: '+visuals+' '+message);
        }
        timing('visualExport',Date.now()-exportStage);
      }
      let mapPath=null,remoteMapPath=null;
      if(campaignMapExporter && roots>=0){
        exportStage=Date.now();
        const explored=campaignMapExplore(zone);if(explored<0)throw new Error('Remote map exploration failed: '+explored);
        mapPath=cfg.sandbox+'/map-'+(cfg.rollingWorld?sampled%16:sampled)+'.json';
        const cells=campaignMapExporter(zone,Memory.allocUtf8String(mapPath));if(cells<0)throw new Error('Campaign map export failed: '+cells);
        remoteMapPath=cfg.sandbox+'/remote-map-'+(cfg.rollingWorld?sampled%16:sampled)+'.json';
        if(campaignMapRemoteExporter(zone,Memory.allocUtf8String(remoteMapPath))!==cells)throw new Error('Remote faction map export failed');
        timing('mapExport',Date.now()-exportStage);
      }
      timing('sceneExport',Date.now()-sceneExportStarted);
      send({type:'world-exported',seq:sampled,path,roots,visualPath,mapPath,remoteMapPath,runtimeHealth:cfg.skipHealth?[]:runtimeHealth(zone),
        pilotHealth:cfg.verifyPilotHealth && roots>0?pilotHealth(zone):[]});
      if(cfg.testRemoteDeath && sampled>=(cfg.beamFixture?40:20) && !remoteDestroyed) {
        remoteDestroyed=true;
        const destroy=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedDestroyRemoteFixture'),
          'int',['pointer']);
        send({type:'remote-fixture-destroyed',result:destroy(zone)});
      }
    }
    if(damageFixture && !damagedFixture && sampled>=5) {
      damagedFixture=true;send({type:'fixture-damage-result',result:damageFixture(zone,8)});
    }
    if(partialDamageFixture && !partiallyDamagedFixture && sampled>=3 && (!cfg.verifyPilotHealth || driveStates.size>0)) {
      partiallyDamagedFixture=true;send({type:'fixture-partial-damage-result',result:partialDamageFixture(zone,cfg.verifyPilotHealth?20008:8)});
    }
    if(worldLoader && pendingWorld && consoleContext) {
      const sceneToken=trySceneUpdate(zone);
      if(sceneToken===0){noteMotionDeferred(zone,'geometry');if(sceneIdleGateEnabled && sceneGateReady)lastSample=0;return;}
      const geometryStarted=Date.now();
      if(applyingFromMenu)menuSnapshotApplies++;
      const world=pendingWorld; pendingWorld=null;
      let clusters;
      let persistentReplica={};
      let presentation={};
      let sceneCommitted=false;
      try{
      if(cfg.persistentReplica){
        let geometryStage=Date.now();
        nativeStage('geometry-remove',world.seq);
        if(cfg.measureMotion){
          const transitions=(world.incremental.transitions||[]).filter(row=>row.ident===0x70000001||row.ident===0x70000002);
          if(transitions.length)send({type:'replica-generation',seq:world.seq,atMs:Date.now(),frame:displayFrames,transitions});
        }
        applyReplicaRemovals(zone,world.incremental,world.seq);
        timing('geometryRemove',Date.now()-geometryStage);geometryStage=Date.now();
        if(world.hasAdditions){
          nativeStage('geometry-append',world.seq);
          // The private Console getter owns an isolated field while the real
          // campaign streamer remains visible to the independent draw thread.
          clusters=worldLoader(zone,consoleContext,Memory.allocUtf8String(world.path));
        }else clusters=zone.add(0x190).readPointer().sub(zone.add(0x188).readPointer()).toInt32()/8;
        timing('geometryAppend',Date.now()-geometryStage);geometryStage=Date.now();
        if(clusters>=0){
          nativeStage('geometry-update',world.seq);
          const plan=world.incremental;
          const prepared=world.preparedIncremental;
          if(!prepared)throw new Error('Persistent replica state was not prepared');
          const updated=replicaUpdater(zone,prepared.poses.buffer,prepared.poses.count,prepared.health.buffer,prepared.health.count);
          if(updated<0)throw new Error('Persistent replica update failed: '+updated);
          const stats=Memory.alloc(24);replicaStats(stats);
          persistentReplica={retained:plan.retained,replaced:plan.replaced,totalPoseUpdates:stats.readU64().toNumber(),
            totalRemovals:stats.add(8).readU64().toNumber(),totalHealthUpdates:stats.add(16).readU64().toNumber()};
          if(cfg.measureMotion){persistentReplica.continuity=plan.continuity||[];persistentReplica.transitions=plan.transitions||[];}
        }
        timing('geometryRuntimeUpdate',Date.now()-geometryStage);
      }else clusters=worldLoader(zone,consoleContext,Memory.allocUtf8String(world.path));
      if(visualApply && clusters>=0 && (!cfg.fastMotion || !lastMotion)) {
        const motion=world.visuals.motion;
        const applyMotion=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedApplyReplicaMotion'),'int',['pointer','pointer','int']);
        const moving=applyMotion(zone,packedBuffer(motion),motion.length/16);if(moving<0)throw new Error('Replica angular motion failed: '+moving);
        const buffers=['blocks','projectiles','thrust'].map(key=>{
          const hex=world.visuals[key],data=[];
          for(let i=0;i<hex.length;i+=2)data.push(parseInt(hex.slice(i,i+2),16));
          const buffer=Memory.alloc(Math.max(1,data.length));if(data.length)buffer.writeByteArray(data);return buffer;
        });
        const applied=visualApply(zone,buffers[0],world.visuals.blocks.length/112,buffers[1],world.visuals.projectiles.length/72,buffers[2],world.visuals.thrust.length/88);
        if(applied<0) {
          const diagnostic=cfg.sandbox+'/rejected-native-visual-'+world.seq+'.lua';worldExporter(zone,Memory.allocUtf8String(diagnostic));
          const message=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPresentationMessage'),'pointer',[])().readUtf8String();
          throw new Error('Native presentation apply failed: '+applied+' '+message);
        }
        const stats=Memory.alloc(72);visualStats(stats);
        presentation={motionRoots:moving,thrustEmissions:stats.readU64().toNumber(),projectileDraws:stats.add(8).readU64().toNumber(),
          turretsApplied:stats.add(16).readU64().toNumber(),lasersApplied:stats.add(24).readU64().toNumber(),
          projectileCount:world.visuals.projectiles.length/72,thrustCount:world.visuals.thrust.length/88};
        presentation.beamRenderCalls=stats.add(32).readU64().toNumber();
        presentation.projectilePass1Calls=projectilePass1Calls;
        presentation.beamStages=Array.from({length:4},(_,i)=>stats.add(40+i*8).readU64().toNumber());
      }
      if(cfg.fastMotion && visualStats){
        const stats=Memory.alloc(72);visualStats(stats);
        presentation={thrustEmissions:stats.readU64().toNumber(),projectileDraws:stats.add(8).readU64().toNumber(),turretsApplied:stats.add(16).readU64().toNumber(),lasersApplied:stats.add(24).readU64().toNumber(),beamRenderCalls:stats.add(32).readU64().toNumber(),localMoverUpdates:localMoverCounter?localMoverCounter().toNumber():0,projectilePass1Calls};
        presentation.beamStages=Array.from({length:4},(_,i)=>stats.add(40+i*8).readU64().toNumber());
      }
      if(!lastMotion)replicaPresentedAt=Date.now();
      // Geometry frames are older than the small motion stream. Restore the
      // newest poses immediately after structural/health updates. An acquired
      // private gate also accepts queued motion: Update entry can miss an idle
      // window that this Update exit has already acquired for geometry.
      if(motionApply){
        if(sceneToken===1 && clusters>=0 && pendingMotion){
          const frame=pendingMotion;pendingMotion=null;pendingMotionQueuedSince=0;applyMotionFrame(zone,frame,true);lastMotion=frame;
        }else if(lastMotion)applyMotionFrame(zone,lastMotion,false);
        if(lastMotion)consumeVisualFrame(zone,true);
      }
      if(cfg.nativeCampaignClient && clusters>=0){
        nativeStage('geometry-bind',world.seq);
        const bind=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedBindReplicaPlayer'),'int',['pointer','uint']);
        const bound=bind(zone,cfg.followPilot);if(bound!==1)throw new Error('Native replica player binding failed: '+bound);
      }
      if(sceneToken===1 && clusters<0)throw new Error('Native gated world loading failed: '+clusters);
      sceneCommitted=clusters>=0;
      }finally{endSceneUpdate(zone,sceneToken,sceneCommitted);}
      let campaignMap={};
      if(campaignMapApply && clusters>=0){
        let mapStage=Date.now();
        const map=world.map;
        const cells=campaignMapApply(zone,packedBuffer(map.radius),map.width,packedBuffer(map.cells),map.width*map.width,packedBuffer(map.regions),map.regions.length/24);
        if(cells<0)throw new Error('Native campaign map apply failed: '+cells);
        const objectives=campaignObjectivesApply(zone,packedBuffer(map.objectives),map.objectives.length/72);
        if(objectives<0)throw new Error('Native campaign objectives apply failed: '+objectives);
        timing('mapApply',Date.now()-mapStage);mapStage=Date.now();
        const verify=cfg.sandbox+'/verified-map-'+world.seq+'.json';
        if(campaignMapExporter(zone,Memory.allocUtf8String(verify))!==cells)throw new Error('Native campaign map readback failed');
        timing('mapReadback',Date.now()-mapStage);
        campaignMap={...world.mapSummary,nativeMapRenders,verifiedPath:verify,menuSnapshotApplies};
      }
      let pilotPointer=null;
      if(cfg.followPilot && clusters>=0) {
        replicaPilot=null;
        const begin=zone.add(0x188).readPointer();
        for(let i=0;i<clusters;i++) {
          const cluster=begin.add(i*8).readPointer();
          if(clusterIdent(cluster)===cfg.followPilot && cluster.add(0x178).readPointer().isNull()) {
            pilotPointer=cluster.toString();
            replicaPilot=[cluster.add(0x30).readDouble(),cluster.add(0x38).readDouble()];break;
          }
        }
      }
      const verifiedPath=cfg.fullReplicaReadback!==false?cfg.sandbox+'/verified-world-'+world.seq+'.lua':null;
      const readbackStarted=Date.now();
      if(!verifiedPath && !replicaRootCountReader)replicaRootCountReader=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedCountReplicaRoots'),'int',['pointer']);
      const verifiedRoots=clusters<0?-1:verifiedPath?worldExporter(zone,Memory.allocUtf8String(verifiedPath)):replicaRootCountReader(zone);
      timing('worldReadback',Date.now()-readbackStarted);
      if(blockRenderCounter)pilotRenderCalls=blockRenderCounter().toNumber();
      if(blockRenderCounter && !cfg.nativeCampaignClient)new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedObservePilot'),'void',['pointer','uint'])(zone,cfg.followPilot);
      let prediction={};if(predictionStats){const stats=Memory.alloc(32);predictionStats(stats);prediction={steps:stats.readDouble(),reconciliations:stats.add(8).readDouble(),maximumCorrection:stats.add(16).readDouble(),tick:stats.add(24).readDouble()};}
      let motionTimeline={};if(motionTimelineStats){const stats=Memory.alloc(32);motionTimelineStats(stats);motionTimeline={duplicateRows:stats.readDouble(),geometryPoseSkips:stats.add(8).readDouble(),maximumCorrection:stats.add(16).readDouble(),maximumAngleCorrection:stats.add(24).readDouble()};}
      const presentationBusy={presenterSkips:presenterBusySkips,traceSkips:traceBusySkips};
      if(presentationGuardStats){const stats=Memory.alloc(24);presentationGuardStats(stats);Object.assign(presentationBusy,{nativePresenterSkips:stats.readDouble(),nativeTraceSkips:stats.add(8).readDouble(),correctedPoseRestorations:stats.add(16).readDouble()});}
      let interpolation={};if(interpolationStatsReader){const stats=Memory.alloc(40);interpolationStatsReader(stats);interpolation={bracketedRoots:stats.readDouble(),boundedExtrapolations:stats.add(8).readDouble(),startupHolds:stats.add(16).readDouble(),staleFreezes:stats.add(24).readDouble(),bufferResets:stats.add(32).readDouble()};}
      let framePacing={};if(framePaceStatsReader){const stats=Memory.alloc(64);framePaceStatsReader(stats);framePacing={calls:stats.readDouble(),waits:stats.add(8).readDouble(),timeouts:stats.add(16).readDouble(),failures:stats.add(24).readDouble(),maximumWaitMs:stats.add(32).readDouble(),maximumRequestedMs:stats.add(40).readDouble(),deadlineResets:stats.add(48).readDouble(),lastWaitResult:stats.add(56).readDouble()};}
      let privateField={};if(replicaFieldStatsReader){const stats=Memory.alloc(24);replicaFieldStatsReader(stats);privateField={privateCalls:stats.readDouble(),forwardedCalls:stats.add(8).readDouble(),rejectedCalls:stats.add(16).readDouble()};}
      if(presentationClockReader){const stats=Memory.alloc(32),ready=presentationClockReader(stats);if(ready===4)lastPresentationClock={localTimeMs:stats.readDouble(),sourceOffsetMs:stats.add(8).readDouble(),motionSourceTimeMs:stats.add(16).readDouble(),visualSourceTimeMs:stats.add(24).readDouble()};else if(ready!==0 && ready!==-7)throw new Error('Native presentation clock stats failed: '+ready);}
      let healthAudit=null;if(cfg.verifyPilotHealth && lastHealthFrame && realtimeHealthAudit){const stats=Memory.alloc(24);realtimeHealthAudit(zone,lastHealthFrame.health.buffer,lastHealthFrame.health.count,stats);healthAudit={blocksCompared:stats.readDouble(),mismatches:stats.add(8).readDouble(),maximumError:stats.add(16).readDouble()};}
      if(clusters>=0 && verifiedRoots===world.roots)configureSceneGate(zone);
      timing('geometryApply',Date.now()-geometryStarted);
      send({type:'world-applied',seq:world.seq,clusters,expectedRoots:world.roots,
        verifiedPath,verifiedRoots,inputSha256:world.sha256,message:worldLoadMessage().readUtf8String(),
        displayFrames,drawCalls,pollCalls,lastView,pilotRenderCalls,replicaPilot,
        frameTiming:{histogram:frameHistogram,longFrames,maxFrameMs},framePacing,privateField,ownership:readOwnershipStats(),localExhaust:readLocalExhaustStats(),replacement:readReplacementStats(),sceneGate:readSceneGateStats(),sceneHandoff:readSceneHandoffStats(),motionAdmission:motionAdmission(),deliveryTiming:deliverySummary(),motionTimeline,simulationPresentationRate:presentationRateReader?presentationRateReader():1,predictedFrames,presentationBusy,presentation,persistentReplica,pilotPointer,campaignMap,motionApplied,prediction,nativeZoneUpdates,nativeHeartbeatCalls,drawThread:nativeDrawThread,swapThread:nativeSwapThread,cameraThread:nativeCameraThread,nativeUpdateThread,healthFrameSeq:lastHealthFrame?lastHealthFrame.seq:0,healthAudit,
        presentationDelayMs,interpolation,presentationClock:lastPresentationClock,comparisonView:lastComparisonView,visualHistoryResets,visualFrameSeq:lastVisualFrame?lastVisualFrame.seq:0,visualFrameApplications,preparedVisualFrames:preparedVisualFrames.length,
        pilotHealth:cfg.verifyPilotHealth && clusters>=0?pilotHealth(zone):[],runtimeHealth:clusters>=0 && !cfg.skipHealth?runtimeHealth(zone):[]});
    }
    if (poseSetter && pendingState && result >= 1) {
      const state = pendingState; pendingState = null;
      const results = state.ships.map(s => poseSetter(zone,s.faction,s.ident,s.x,s.y,s.vx,s.vy,s.angle));
      send({type:'replica-state-applied', seq:state.seq, results, ships:state.ships});
    }
    if (cfg.streamState && !cfg.identitiesOnly && result >= 1) {
      const begin = zone.add(0x188).readPointer();
      const ships = [];
      const count=Math.min(result,4096);
      for (let i=0;i<count;i++) {
        const cluster=begin.add(i*8).readPointer();
        const ident=clusterIdent(cluster);
        if (!ident) continue;
        const parent=cluster.add(0x178).readPointer();
        const body=parent.isNull()?cluster:parent;
        ships.push({ident,faction:cluster.add(0x118).readS32(), x:body.add(0x30).readDouble(),
          y:body.add(0x38).readDouble(),vx:body.add(0x40).readDouble(),vy:body.add(0x48).readDouble(),angle:body.add(0x60).readFloat()});
      }
      send({type:'authoritative-state',seq:sampled,ships});
    }
    if (mover && !movesApplied && result >= 2) {
      movesApplied = true;
      for (const command of cfg.moves || []) send({type:'waypoint-result', ...command,
        result:mover(zone, command.ownerFaction, command.targetFaction, command.x, command.y)});
    }
    if(!cfg.directControl && result>=2) dispatchControls(zone);
    if (sampled <= 5 || sampled % 30 === 0) send({type:'sample-result', clusters:result, sample:sampled});
}
if(cfg.keepHostRunningInMenus){
  const active=new NativeFunction(game.base.add(0x119f90),'bool',['pointer']);
  const updateZone=new NativeFunction(game.getExportByName('?Update@GameZone@@QEAAXXZ'),'void',['pointer']);
  const show=new NativeFunction(game.base.add(0xb6f20),'uint64',['pointer','uint','uint']);
  const pop=new NativeFunction(game.base.add(0x118070),'void',['pointer','pointer','float']);
  let originalPlayer=null,brake=null,stage=0,opened=0,modal=null;
  const playerAddress=game.getExportByName('?playerUpdate@AI@@AEAA_NXZ');
  const playerOriginal=Interceptor.replaceFast(playerAddress,new NativeCallback(ai=>{
    if(hostMenuActive && campaignAuthorityZone){
      if(!brake)brake=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedBrakeHostInMenu'),'int',['pointer','pointer']);
      const handled=brake(ai,campaignAuthorityZone);if(handled<0)throw new Error('Host menu brake failed: '+handled);
      if(handled===1){hostMenuBrakes++;return 1;}
    }
    return originalPlayer(ai);
  },'bool',['pointer']));
  originalPlayer=new NativeFunction(playerOriginal,'bool',['pointer']);
  Interceptor.attach(game.base.add(0x11c700),{onEnter(args){if(campaignAuthorityZone && args[0].add(8).readPointer().equals(campaignAuthorityZone))hostFlightState=args[0];}});
  Interceptor.attach(game.base.add(0x281920),{onEnter(args){if(hostFlightState && args[0].equals(hostFlightState)){
    hostFlightState=null;hostMenuActive=false;campaignAuthorityEnded=true;campaignAuthorityZone=null;
    send({type:'error',description:'Host game left its campaign. Host the saved galaxy again to continue.'});
  }}});
  Interceptor.attach(game.base.add(0x118300),{onLeave(){
    if(!hostFlightState || !campaignAuthorityZone || hostMenuBusy)return;
    hostMenuBusy=true;
    try{
      if(cfg.testHostMenus && sampled>=20){
        if([0,2].includes(stage) && active(hostFlightState)){
          const tab=stage===0?1:0x10;if(show(hostFlightState,tab,tab).toNumber()!==1)throw new Error('Host menu could not open');
          const list=hostFlightState.add(0x10).readPointer();modal=list.add(8).readPointer().add(list.add(0x70).readS32()*8).readPointer();
          opened=Date.now();stage++;send({type:'host-menu-test',action:'opened',tab,hostMenuUpdates,hostMenuBrakes,hostSnapshots:sampled,thread:Process.getCurrentThreadId(),hostUpdateThread});
        }else if([1,3].includes(stage) && Date.now()-opened>=3000){
          pop(modal.add(0x10).readPointer(),modal,0.5);
          send({type:'host-menu-test',action:'closed',tab:stage===1?1:0x10,hostMenuUpdates,hostMenuBrakes,hostSnapshots:sampled,thread:Process.getCurrentThreadId(),hostUpdateThread});stage++;modal=null;
        }
      }
      hostMenuActive=!active(hostFlightState);
      if(hostMenuActive){
        if(Process.getCurrentThreadId()!==hostUpdateThread)throw new Error('Host menu simulation thread differs');
        // Calls initiated by this script must explicitly run the same network
        // phase as the normal update hook. Skip the hook while this is active.
        hostMenuUpdates++;beforeZoneUpdate(campaignAuthorityZone);
        updateZone(campaignAuthorityZone);afterZoneUpdate(campaignAuthorityZone);
      }
    }finally{hostMenuBusy=false;}
  }});
}
if(cfg.renderOnly) {
  const step=game.base.add(0x2eca30),prefix=[0x40,0x55,0x48,0x81,0xec,0xa0,0,0,0];
  const actual=new Uint8Array(step.readByteArray(prefix.length));
  if(prefix.some((b,i)=>actual[i]!==b)) throw new Error('Physics step signature differs');
  // Keep zone bookkeeping/render scheduling while stopping local physics and
  // block simulation. The host alone advances authoritative combat state.
  if(load(Memory.allocUtf16String(cfg.dll)).isNull()) throw new Error('Replica DLL load failed');
  const replicaDll=Process.getModuleByName('RepopulatedDiagnostic.dll');
  installWireHex(replicaDll);
  if(thrustAuditIdent)configureThrustAudit(replicaDll);
  if(cfg.measureRendering){
    const render=game.getExportByName('?render@Block@@QEBAXPEAU?$MeshPair@UVertexPos2ColorTime@@UVertexPosColor@@@@@Z');
    const original=Interceptor.replaceFast(render,replicaDll.getExportByName('RepopulatedObserveBlockRender'));
    new NativeFunction(replicaDll.getExportByName('RepopulatedSetBlockRenderOriginal'),'void',['pointer'])(original);
    blockRenderCounter=new NativeFunction(replicaDll.getExportByName('RepopulatedBlockRenderCalls'),'uint64',[]);
  }
  if(cfg.fastMotion){
    motionBegin=new NativeFunction(replicaDll.getExportByName('RepopulatedBeginMotionFrame'),'void',['uint','float','double','double']);
    new NativeFunction(replicaDll.getExportByName('RepopulatedConfigurePresentationDelay'),'void',['float'])(presentationDelayMs);
    visualBegin=new NativeFunction(replicaDll.getExportByName('RepopulatedBeginVisualFrame'),'void',['double']);
    viewSourceTimeReader=new NativeFunction(replicaDll.getExportByName('RepopulatedViewSourceTime'),'double',[]);
    interpolationStatsReader=new NativeFunction(replicaDll.getExportByName('RepopulatedInterpolationStats'),'void',['pointer']);
    presentationClockReader=new NativeFunction(replicaDll.getExportByName('RepopulatedPresentationClockStats'),'int',['pointer']);
    motionTimelineStats=new NativeFunction(replicaDll.getExportByName('RepopulatedMotionTimelineStats'),'void',['pointer']);
    presentationRateReader=new NativeFunction(replicaDll.getExportByName('RepopulatedSimulationPresentationRate'),'double',[]);
    presentationGuardStats=new NativeFunction(replicaDll.getExportByName('RepopulatedPresentationGuardStats'),'void',['pointer']);
    const pacingStatsExport=replicaDll.findExportByName('RepopulatedNativePaceStats');
    if(pacingStatsExport)framePaceStatsReader=new NativeFunction(pacingStatsExport,'void',['pointer']);
    const exhaustStatsExport=replicaDll.findExportByName('RepopulatedLocalExhaustStats');
    if(exhaustStatsExport){localExhaustStatsReader=new NativeFunction(exhaustStatsExport,'void',['pointer']);localExhaustStatsBuffer=Memory.alloc(5*8);}
    motionApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyMotionFrame'),'int',['pointer','pointer','int']);
    const motionMessageExport=replicaDll.findExportByName('RepopulatedMotionApplyMessage');
    if(motionMessageExport)motionApplyMessage=new NativeFunction(motionMessageExport,'pointer',[]);
    realtimeVisualApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyRealtimePresentation'),'int',['pointer','pointer','int','pointer','int','pointer','int']);
    realtimeHealthApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyRealtimeHealth'),'int',['pointer','pointer','int']);
    realtimeHealthAudit=new NativeFunction(replicaDll.getExportByName('RepopulatedAuditRealtimeHealth'),'void',['pointer','pointer','int','pointer']);
    realtimeMoversApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyMovers'),'int',['pointer','int']);
    if(cfg.nativeCampaignClient)realtimeMoverWindowApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyMoverWindow'),'int',['pointer','int','double','pointer','int','double']);
    localMoverCounter=new NativeFunction(replicaDll.getExportByName('RepopulatedLocalMoverUpdates'),'uint64',[]);
    if(cfg.predictPilot){
      new NativeFunction(replicaDll.getExportByName('RepopulatedEnablePrediction'),'void',['bool'])(1);
      predictionTick=new NativeFunction(replicaDll.getExportByName('RepopulatedPredictionTick'),'uint',[]);
      predictionAck=new NativeFunction(replicaDll.getExportByName('RepopulatedPredictionAck'),'void',['uint','uint']);
      predictionStats=new NativeFunction(replicaDll.getExportByName('RepopulatedPredictionStats'),'void',['pointer']);
    }
    const effect=game.base.add(0x1cc440),prefix=[0x48,0x8b,0xc4,0x48,0x89,0x58,0x08,0x55,0x48,0x8d,0x68,0xc1];
    const actual=new Uint8Array(effect.readByteArray(prefix.length));if(prefix.some((b,i)=>actual[i]!==b))throw new Error('Local native exhaust signature differs');
    const original=Interceptor.replaceFast(effect,replicaDll.getExportByName('RepopulatedLocalThrust'));
    new NativeFunction(replicaDll.getExportByName('RepopulatedSetThrustOriginal'),'void',['pointer'])(original);
  }
  if(cfg.predictPresentation) replicaPresenter=new NativeFunction(replicaDll.getExportByName('RepopulatedPresentReplica'),'int',['pointer','float','uint','pointer']);
  if(cfg.replicatePresentation) {
    visualApply=new NativeFunction(replicaDll.getExportByName('RepopulatedApplyPresentation'),'int',['pointer','pointer','int','pointer','int','pointer','int']);
    visualTick=new NativeFunction(replicaDll.getExportByName('RepopulatedTickPresentation'),'int',['pointer']);
    visualProjectiles=new NativeFunction(replicaDll.getExportByName('RepopulatedDrawReplicaProjectiles'),'int',['pointer']);
    visualStats=new NativeFunction(replicaDll.getExportByName('RepopulatedPresentationStats'),'void',['pointer']);
    Interceptor.attach(game.getExportByName('?renderProjectilesPass0@GameZone@@QEAAXAEBUView@@AEAU?$TriMesh@UVertexPosColorLuma@@@@@Z'),{
      onEnter(args){this.zone=args[0];},onLeave(){if(visualProjectiles(this.zone)<0)throw new Error('Replica projectile rendering failed');}
    });
    const beams=new NativeFunction(replicaDll.getExportByName('RepopulatedRenderReplicaBeams'),'int',['pointer','pointer','pointer']);
    Interceptor.attach(game.getExportByName('?renderProjectilesPass1@GameZone@@QEBAXAEBUView@@AEAU?$TriMesh@UVertexPosColorLuma@@@@@Z'),{
      onEnter(args){projectilePass1Calls++;const result=beams(args[0],args[2],args[1]);if(result<0)throw new Error('Native replica beam rendering failed: '+result);}
    });
  }
  Interceptor.replace(step,replicaDll.getExportByName('RepopulatedReplicaPhysicsStep'));
  Interceptor.replace(game.getExportByName('?update@Block@@QEAA_NI@Z'),replicaDll.getExportByName('RepopulatedReplicaBlockUpdate'));
  Interceptor.replace(game.getExportByName('?update@AI@@QEAAX_N@Z'),replicaDll.getExportByName('RepopulatedReplicaAIUpdate'));
}
function consumeMotion(zone,opportunity='update'){
    if(pendingMotion && campaignReplicaZone && !zone.equals(campaignReplicaZone))streamStats.zoneSkips++;
    if(!motionApply || !replicaInitialized || (campaignReplicaZone && !zone.equals(campaignReplicaZone)) || (!pendingMotion && !lastMotion))return;
    if(sceneGateReady && !pendingMotion){
      const frame=currentVisualFrame();
      if(!frame || (lastVisualFrame && frame.seq===lastVisualFrame.seq))return;
    }
    const sceneToken=trySceneUpdate(zone);if(sceneToken===0){noteMotionDeferred(zone,opportunity);return;}
    let started=0;
    let sceneCommitted=false;
    try{
    if(pendingMotion){
      started=Date.now();
      const frame=pendingMotion;pendingMotion=null;pendingMotionQueuedSince=0;applyMotionFrame(zone,frame);lastMotion=frame;
    }
    consumeVisualFrame(zone);
    sceneCommitted=true;
    }finally{endSceneUpdate(zone,sceneToken,sceneCommitted);}
    if(started){const elapsed=Date.now()-started;timing('motionApply',elapsed);streamStats.maxApplyMs=Math.max(streamStats.maxApplyMs,elapsed);}
}
function beforeZoneUpdate(zone) {
    nativeZoneUpdates++;
    if(campaignAuthorityEnded)return;
    if(cfg.campaignRemote && campaignAuthorityZone && !zone.equals(campaignAuthorityZone))return;
    if(cfg.keepHostRunningInMenus)hostUpdateThread=Process.getCurrentThreadId();
    // The native campaign already paces physics. An added sleep here halved
    // its simulation clock while the independent renderer kept running.
    if ((cfg.networkControl || cfg.replica) && !cfg.frameLimit && !cfg.campaignRemote) Thread.sleep(1/60);
    if(cfg.nativeCampaignClient && campaignReplicaZone && zone.equals(campaignReplicaZone))nativeUpdateThread=Process.getCurrentThreadId();
    if(cfg.nativeCampaignClient && campaignReplicaZone && zone.equals(campaignReplicaZone)){campaignConsole.add(8).writePointer(zone);consoleContext=campaignConsole;}
    consumeMotion(zone,'entry');
    if(cfg.directControl) dispatchControls(zone);
    if(driver) for(const state of driveStates.values()) {
      const stale=Date.now()-state.received>500;
      const result=driveCommand(zone,state,stale);
      if(result<0) throw new Error('Native direct control failed: '+result);
      if(stale && !state.expired) {state.expired=true;send({type:'drive-expired',faction:state.ownerFaction});}
    }
}
function afterZoneUpdate(zone) {
    if(campaignAuthorityEnded)return;
    if(cfg.campaignRemote && campaignAuthorityZone && !zone.equals(campaignAuthorityZone))return;
    reportHostAIStats();
    reportHostOwnershipStats();
    if(pendingCheckpoint) {
      const request=pendingCheckpoint;pendingCheckpoint=null;
      ensureSceneIdentities(zone);
      const save=game.base.add(0x3cf700).readPointer();
      const checkpoint=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedCheckpointCampaign'),
        'int',['pointer','pointer','pointer','pointer']);
      const files=checkpoint(zone,save,Memory.allocUtf16String(cfg.sandbox+'/Reassembly/data/save0'),Memory.allocUtf16String(request.path));
      const exportPlayers=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedExportPlayerShips'),'int',['pointer','pointer']);
      const roots=files>=3?exportPlayers(zone,Memory.allocUtf8String(request.path+'/player-ships.lua')):-1;
      if(files>=3 && cfg.syncCampaignMap && campaignMapExporter(zone,Memory.allocUtf8String(request.path+'/campaign-map.json'))<0)throw new Error('Checkpoint map export failed');
      if(files>=3 && cfg.syncCampaignMap && campaignMapRemoteExporter(zone,Memory.allocUtf8String(request.path+'/remote-map.json'))<0)throw new Error('Checkpoint remote exploration export failed');
      send({type:'checkpoint-complete',id:request.id,path:request.path,files,roots,playerHealth:files>=3 && roots===2?playerHealth(zone):[]});
    }
    if(cfg.streamMotion && loaded && Date.now()-lastMotionExport>=50){
      const exportStarted=Date.now();
      if(lastMotionSampled)timing('exportInterval',exportStarted-lastMotionSampled);lastMotionSampled=exportStarted;
      lastMotionExport=exportStarted;
      if(!motionRead){motionRead=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedReadMotion'),'int',['pointer','uint','float','pointer','int']);motionBuffer=Memory.alloc(4096*44);}
      if(!monotonicClock)monotonicClock=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedMonotonicMillis'),'double',[]);
      let exportStage=Date.now();
      const radius=Math.min(20000,clientViewRadius+1500),sourceTimeMs=Math.floor(monotonicClock()),simTimeMs=Math.round(zone.add(0x158).readFloat()*1000);
      const count=motionRead(zone,cfg.interestIdent,radius,motionBuffer,4096);
      timing('motionRead',Date.now()-exportStage);exportStage=Date.now();
      if(count<0)throw new Error('Native motion export failed: '+count);
      if(count){
        if(!realtimeRead){realtimeRead=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedReadRealtime'),'int',['pointer','uint','float','pointer']);realtimeBuffer=Memory.alloc(16+4096*56+2048*36+4096*16+65536*16);}
        const state=realtimeRead(zone,cfg.interestIdent,radius,realtimeBuffer);if(state<0)throw new Error('Native realtime export failed: '+state);
        timing('realtimeRead',Date.now()-exportStage);exportStage=Date.now();
        const packed={};let offset=16;
        [['blocks',56,4096],['projectiles',36,2048],['movers',16,4096],['health',16,65536]].forEach(([key,size,limit],i)=>{const n=realtimeBuffer.add(i*4).readU32();if(n>limit)throw new Error('Realtime export bounds');packed[key]=hexBuffer(realtimeBuffer.add(offset),n*size);offset+=size*limit;});
        const poses=hexBuffer(motionBuffer,count*44);timing('motionEncode',Date.now()-exportStage);
        timing('motionExport',Date.now()-exportStarted);
        send({type:'native-motion',seq:++motionSequence,sourceTimeMs,simTimeMs,poses,inputSeq:latestInputSequence,inputTick:latestInputTick,...packed,deliveryTiming:deliverySummary(),nativeThreads:{update:hostUpdateThread,draw:nativeDrawThread,swap:nativeSwapThread},hostFrameTiming:{histogram:frameHistogram,longFrames,maxFrameMs}});
      }
    }
    // Entry can coincide with rendering for several updates. The private
    // fixture also tries its nonblocking idle slot after native Update returns.
    if(sceneGateReady && cfg.nativeCampaignClient && replicaInitialized && campaignReplicaZone && zone.equals(campaignReplicaZone))consumeMotion(zone,'exit');
    sample(zone);
    if(cfg.pilotInput && !cfg.nativeCampaignClient && Date.now()-lastPilotInput>=100) {
      lastPilotInput=Date.now();
      if(mouseWindow && !mouseWindow.isNull() && lastView) {
        const w=Memory.alloc(4),h=Memory.alloc(4);getWindowSize(mouseWindow,w,h);
        const width=w.readS32(),height=h.readS32();
        if(width>0 && height>0) mouseOffset=[(mouseX/width-0.5)*lastView[2]*lastView[8],(0.5-mouseY/height)*lastView[3]*lastView[8]];
      }
      const menuPaused=openEditors.size>0;
      if(menuPaused){pilotKeys.clear();mouseHeld=false;}
      send({type:'native-pilot-input',x:Number(pilotKeys.has(100))-Number(pilotKeys.has(97)),
        y:Number(pilotKeys.has(119))-Number(pilotKeys.has(115)),fire:mouseHeld,aim:menuPaused?null:mouseOffset,menuPaused,textInputActive:textInputActive?textInputActive():0});
    }
}
Interceptor.attach(game.getExportByName('?Update@GameZone@@QEAAXXZ'), {
  onEnter(args){this.zone=args[0];if(!hostMenuBusy)beforeZoneUpdate(this.zone);},
  onLeave(){if(!hostMenuBusy)afterZoneUpdate(this.zone);}
});
let aiForward=null,aiOverride=null;
if(cfg.directControl && !cfg.replica && !cfg.fireTest) {
  const owned=new Set(cfg.ownedFactions || [7,8]);let vanillaCalls=0;
  aiOverride=new NativeCallback((ai,force)=>{
    const command=ai.add(0x278).readPointer();
    if(!command.isNull()) {
      const cluster=command.add(0xb8).readPointer();
      if(!cluster.isNull()) {
        const faction=cluster.add(0x118).readS32(),pilot=(cfg.activeShips || {})[faction];
        if(owned.has(faction) && (!pilot || clusterIdent && clusterIdent(cluster)===pilot)) {
          if(remoteControlEnabled) {
            // GameZone clears weapon enables before this AI phase. Apply a
            // held weapon here, as native playerUpdate does, so charging and
            // beam weapons survive into the following block update.
            const state=fireStates.get(faction);
            if(state && fire) {
              if(state.action==='native'){
                const now=Date.now();
                state.weapons.forEach((row,i)=>{
                  const pulse=nativeWeaponPulses.get(row[0]);
                  state.nativeWeaponBuffer.add(i*32+4).writeU32((row[1]|(pulse && now-pulse.received<=500?pulse.mask:0))>>>0);
                });
                const result=nativeWeapons(cluster.add(8).readPointer(),faction,pilot,state.nativeWeaponBuffer,state.weapons.length,Number(Date.now()-state.received>500));
                nativeWeaponPulses.clear();
                if(result<0)throw new Error('Native weapon intent rejected: '+result);
                if(state.report){send({type:'network-fire-result',seq:state.seq,result});state.report=false;}
                return;
              }
              const held=state.held && Date.now()-state.received<=500;
              if(!held && releaseWeapons && releaseWeapons(cluster,faction,pilot)<0)throw new Error('Native weapon release failed');
              const result=held ? fireCommand(cluster.add(8).readPointer(),state):0;
              if(state.report){send({type:'network-fire-result',...state,result});state.report=false;}
            }
            return;
          }
          vacantAICalls++;
        }
      }
    }
    aiForward(ai,force);vanillaCalls++;hostAIOriginalCalls++;
    if(!hostAIStatsReader && vanillaCalls===300) send({type:'vanilla-ai-preserved',calls:vanillaCalls});
  },'void',['pointer','bool']);
  let replacement=aiOverride,configure=null,ownedRows=null,entryPrefix=null;
  const aiTarget=game.getExportByName('?update@AI@@QEAAX_N@Z');
  if(cfg.campaignRemote){
    const expected=[0x40,0x53,0x55,0x48,0x83,0xec,0x28,0x4c,0x8b,0x81,0x78,0x02,0,0,0x0f,0xb6,0xea];
    const actual=new Uint8Array(aiTarget.readByteArray(expected.length));
    if(expected.some((byte,i)=>actual[i]!==byte))throw new Error('Native host AI entry signature differs');
    entryPrefix=Array.from(actual,b=>b.toString(16).padStart(2,'0')).join('');
    if(load(Memory.allocUtf16String(cfg.dll)).isNull())throw new Error('Native AI filter DLL load failed');
    const dll=Process.getModuleByName('RepopulatedDiagnostic.dll'),factions=Array.from(owned);
    if(!factions.length || factions.length>16 || factions.some(f=>!Number.isInteger(f)||f<=0||f>0x7fffffff))throw new Error('Invalid native AI ownership');
    ownedRows=Memory.alloc(factions.length*8);
    factions.forEach((f,i)=>{
      const pilot=(cfg.activeShips || {})[f] || 0;
      if(!Number.isInteger(pilot)||pilot<0||pilot>0xffffffff)throw new Error('Invalid native AI pilot identity');
      ownedRows.add(i*8).writeS32(f);ownedRows.add(i*8+4).writeU32(pilot);
    });
    replacement=dll.getExportByName('RepopulatedHostAIUpdate');
    configure=new NativeFunction(dll.getExportByName('RepopulatedConfigureHostAI'),'int',['pointer','pointer','pointer','int']);
    hostAIStatsReader=new NativeFunction(dll.getExportByName('RepopulatedReadHostAIStats'),'void',['pointer']);
    hostAIStatsBuffer=Memory.alloc(32);
  }
  const original=Interceptor.replaceFast(aiTarget,replacement);
  aiForward=new NativeFunction(original,'void',['pointer','bool']);
  if(configure){
    let originalBytes;try{originalBytes=hexBuffer(original,64);}catch(error){originalBytes='unreadable: '+error.message;}
    send({type:'native-ai-prefilter-trampoline',entry:aiTarget.toString(),entryPrefix,original:original.toString(),originalBytes});
    try{
      // C retains a raw address; keep the NativeCallback referenced by the
      // script's live RPC state rather than relying on the C pointer alone.
      hostAIOwnedCallback=aiOverride;
      const configured=configure(original,hostAIOwnedCallback,ownedRows,owned.size);
      if(configured<0)throw new Error('Native AI filter configuration failed: '+configured);
    }catch(error){
      Interceptor.revert(aiTarget);hostAIStatsReader=null;hostAIOwnedCallback=null;
      send({type:'native-ai-prefilter-reverted',reason:error.message});throw error;
    }
    send({type:'native-ai-prefilter-installed',ownedFactions:Array.from(owned),activeShips:cfg.activeShips || {}});
  }
}
else if (!cfg.renderOnly && (cfg.replica || cfg.fireTest)) Interceptor.replace(game.getExportByName('?update@AI@@QEAAX_N@Z'), new NativeCallback(() => {}, 'void', ['pointer','bool']));
else if(!cfg.renderOnly) Interceptor.attach(game.getExportByName('?update@AI@@QEAAX_N@Z'), {
  onEnter(args) { this.zone = args[0].add(0x228).readPointer(); },
  onLeave() { if (!this.zone.isNull()) sample(this.zone); }
});
send({type:'instrumentation-ready'});
if(cfg.trackPlayer) {
  let emptyHashReported=false;
  Interceptor.attach(game.base.add(0x1042f0),{
    onEnter(args) {
      if(!emptyHashReported && args[0].add(0x18).readPointer().equals(args[0].add(0x20).readPointer())) {
        emptyHashReported=true;
        send({type:'empty-spatial-hash',caller:this.returnAddress.sub(game.base).toString(),
          cellSize:args[0].add(0x30).readFloat(),inverseSize:args[0].add(0x34).readFloat(),
          width:args[0].add(0x38).readU32(),backtrace:Thread.backtrace(this.context,Backtracer.ACCURATE).slice(0,8).map(p=>p.sub(game.base).toString())});
      }
    }
  });
  let spawned=false;
  Interceptor.attach(game.base.add(0x1dd560),{
    onEnter(args) {
      if(spawned) return;
      spawned=true;
      if(!redirects) throw new Error('Campaign isolation not confirmed');
      if(load(Memory.allocUtf16String(cfg.dll)).isNull()) throw new Error('Campaign DLL load failed');
      new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedSetSharedExploration'),'int',['int'])(cfg.sharedExploration?1:0);
      if(cfg.campaignRemote)campaignAuthorityZone=args[1];
      const save=game.base.add(0x3cf700).readPointer();
      const blueprint=save.add(0x20).readPointer(),ident=save.add(0xd0).readU32();
      if(cfg.restoreCampaignMap){
        const map=cfg.restoreCampaignMap;
        const apply=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedApplyCampaignMap'),'int',['pointer','pointer','int','pointer','int','pointer','int']);
        const cells=apply(args[1],packedBuffer(map.radius),map.width,packedBuffer(map.cells),map.width*map.width,packedBuffer(map.regions),map.regions.length/24);
        if(cells<0)throw new Error('Saved campaign exploration restore failed: '+cells);
        send({type:'campaign-map-restored',cells});
      }
      if(cfg.restoreRemoteMap){
        const map=cfg.restoreRemoteMap;
        const restore=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedRestoreRemoteCampaignMap'),'int',['pointer','pointer','int']);
        const cells=restore(args[1],packedBuffer(map.cells),map.width*map.width);
        if(cells<0)throw new Error('Saved remote exploration restore failed: '+cells);
        send({type:'remote-map-restored',cells});
      }
      const position=Memory.alloc(8);position.writePointer(args[2]);
      const spawn=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName(cfg.nativeCampaignClient?'RepopulatedSpawnRemoteFixture':'RepopulatedSpawnCampaignFixture'),
         'int',cfg.nativeCampaignClient?['pointer','pointer','float','float']:['pointer','pointer','uint','float','float']);
      send({type:'campaign-fixture-spawn',ident,result:cfg.nativeCampaignClient?spawn(args[1],blueprint,position.readFloat(),position.add(4).readFloat()):spawn(args[1],blueprint,ident,position.readFloat(),position.add(4).readFloat())});
      if(cfg.nativeCampaignClient){
        campaignReplicaZone=args[1];
        const streamer=args[1].add(0x248).readPointer();
        if(streamer.isNull() || !streamer.readPointer().add(0x10).readPointer().equals(game.base.add(0x2230f0)))throw new Error('Campaign streamer ABI differs');
        const stopStream=new NativeCallback(()=>0,'uint64',['pointer','uint64']);
        Interceptor.replace(game.base.add(0x2230f0),stopStream);
        send({type:'replica-sector-streaming-disabled'});
      }
      if(cfg.campaignRemote) {
        const spawnRemote=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedSpawnRemoteFixture'),
          'int',['pointer','pointer','float','float']);
        const offset=cfg.remoteOffset || [3000,3000];
        send({type:'campaign-remote-spawn',result:spawnRemote(args[1],blueprint,position.readFloat()+offset[0],position.add(4).readFloat()+offset[1])});
      }
      if(cfg.verifyCampaignRestore) {
        const exportWorld=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedExportPlayerShips'),'int',['pointer','pointer']);
        const path=cfg.sandbox+'/loaded-before-continue.lua';
        send({type:'campaign-restore-scene',path,roots:exportWorld(args[1],Memory.allocUtf8String(path)),playerHealth:playerHealth(args[1])});
      }
    }
  });
  let playerCalls=0;
  let lastIntentSentAt=0;
  // replaceFast owns this entry on a host that keeps simulation running in
  // overlays. Frida cannot also attach to that replaced entry. Native client
  // intent capture remains attached to its unchanged Player routine.
  if(!cfg.keepHostRunningInMenus)Interceptor.attach(game.getExportByName('?playerUpdate@AI@@AEAA_NXZ'),{
    onEnter(args) {
      this.ai=args[0];
      playerCalls++;
      if(playerCalls<=5 || playerCalls%300===0) {
        const command=args[0].add(0x278).readPointer();
        const cluster=command.add(0xb8).readPointer();
        send({type:'native-player-update',calls:playerCalls,faction:cluster.add(0x118).readS32(),
              x:cluster.add(0x30).readDouble(),y:cluster.add(0x38).readDouble()});
      }
    },
    onLeave() {
      if((cfg.testLocalPrediction || cfg.testSmoothFlight) && replicaInitialized){
        if(!predictionTestAt)predictionTestAt=Date.now();
        if(!predictionTestDriver)predictionTestDriver=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedDriveNative'),'int',['pointer','int','uint','uint','pointer','pointer','bool']);
        const destination=Memory.alloc(24),precision=Memory.alloc(16);
        let desired=[0,0,Date.now()-predictionTestAt<4000?200:0,0,0,0];
        if(cfg.testSmoothFlight){
          if(!smoothFlightStarted)smoothFlightStarted=Date.now();
          const elapsed=(Date.now()-smoothFlightStarted)/1000,cycle=Math.floor(elapsed/20),phaseTime=elapsed%20;
          const phase=phaseTime<8?'forward':phaseTime<16?'turn':'brake';
          const heading=cycle*Math.PI/2+(phase==='forward'?0:phase==='turn'?(phaseTime-8)*Math.PI/16:Math.PI/2);
          const speed=phase==='brake'?200*Math.pow(Math.max(0,1-(phaseTime-16)/3),2):200;
          desired=[0,0,Math.cos(heading)*speed,Math.sin(heading)*speed,Math.atan2(Math.sin(heading),Math.cos(heading)),0];
          if(phase!==smoothFlightPhase || cycle!==smoothFlightCycle){
            smoothFlightPhase=phase;smoothFlightCycle=cycle;
            send({type:'flight-fixture-phase',phase,cycle,elapsedSeconds:elapsed,phaseSeconds:phaseTime});
          }
        }
        desired.forEach((v,i)=>destination.add(i*4).writeFloat(v));
        [10,10,0.01,0.01].forEach((v,i)=>precision.add(i*4).writeFloat(v));
        const zone=this.ai.add(0x228).readPointer();
        const driven=predictionTestDriver(zone,20008,cfg.followPilot,0x106,destination,precision,0);
        if(driven!==1)throw new Error('Native prediction fixture drive failed: '+driven);
      }
      if(cfg.captureNativeIntent && Date.now()-lastIntentSentAt>=33) {
        lastIntentSentAt=Date.now();const ai=this.ai;
        const command=ai.add(0x278).readPointer(),cluster=command.add(0xb8).readPointer();
        const destination=Array.from({length:6},(_,i)=>ai.add(0x2bc+i*4).readFloat());
        const precision=Array.from({length:4},(_,i)=>ai.add(0x2a8+i*4).readFloat());
        let weapons=[],weaponFeatures=[];
        if(cfg.nativeCampaignClient){
          destination[0]-=cluster.add(0x30).readDouble();destination[1]-=cluster.add(0x38).readDouble();
          weapons=cfg.testSmoothFlight?nativeManualWeapons.map(row=>[row[0],0,...row.slice(2)]):nativeManualWeapons;weaponFeatures=nativeManualFeatures;
          const alive=new Set(weapons.map(row=>row[0]));for(const id of nativeWeaponTargets.keys())if(!alive.has(id))nativeWeaponTargets.delete(id);
        }
        let viewRadius=5000;
        if(lastView && lastView[3]>0){
          const width=lastView[2]*lastView[8]-(lastView[2]/lastView[3])*lastView[9],height=lastView[3]*lastView[8]-lastView[9];
          viewRadius=Math.min(18500,Math.max(1000,Math.hypot(width,height)*0.6));
        }
        sendNativeIntent({type:'native-navigation-intent',ident:clusterIdent(cluster),dimensions:ai.add(0x2b8).readU32(),destination,precision,weapons,weaponFeatures,viewRadius,clientTick:predictionTick?predictionTick():0,
              flightFixture:cfg.testSmoothFlight?{phase:smoothFlightPhase,cycle:smoothFlightCycle,elapsedSeconds:smoothFlightStarted?(Date.now()-smoothFlightStarted)/1000:0}:null,
              vx:ai.add(0x2c4).readFloat(),vy:ai.add(0x2c8).readFloat(),angle:ai.add(0x2cc).readFloat()});
      }
    }
  });
}
if(cfg.replica)setInterval(()=>send({type:'native-progress',displayFrames,nativeZoneUpdates,motionApplied,
                                    stages:Array.from(nativeLastStages.values())}),1000);
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--headless-mode', type=int, default=1, choices=range(0, 10),
                        help='Engine mode: 0 selects rendered sandbox; 1 selects verified headless sandbox')
    parser.add_argument('--load-slot',type=int,choices=range(20),help='Investigate campaign startup in a fresh isolated profile only')
    parser.add_argument('--fresh-campaign-fixture',action='store_true',help='Generate an experimental campaign save in the private test profile')
    parser.add_argument('--campaign-remote-test',action='store_true',help='Drive a second empire over TCP inside the fresh rendered campaign')
    parser.add_argument('--input', type=Path, help='Ship/fleet input for headless engine experiments')
    parser.add_argument('--tournament', action='store_true', help='Request the game headless tournament simulator')
    parser.add_argument('--test-control', action='store_true', help='Apply a single velocity command to the second observed command ship')
    parser.add_argument('--sandbox-commands', help='Experimental inline sandbox command script; syntax is under investigation')
    parser.add_argument('--sandbox-file', type=Path, help='Read the experimental sandbox commands from this file')
    parser.add_argument('--two-ship-sandbox', action='store_true', help='Run the verified headless two-ship simulation fixture')
    parser.add_argument('--restore-level', type=Path, help='Copy a native level checkpoint into the isolated profile and load it')
    parser.add_argument('--report-file', type=Path, help='Write the successful run summary to this path')
    parser.add_argument('--faction-control-test', action='store_true', help='Test ownership rejection and separate native faction waypoints')
    parser.add_argument('--network-control-test', action='store_true', help='Exercise real TCP clients driving the native waypoint fixture')
    parser.add_argument('--same-content-factions', action='store_true', help='Test two distinct empires using the same base-faction ship')
    parser.add_argument('--weapon-fire-test', action='store_true', help='Test native aim/fire with vanilla AI suppressed')
    parser.add_argument('--cluster-export-test', action='store_true', help='Export an actual ship using the native engine serializer')
    args = parser.parse_args()
    if args.campaign_remote_test:
        args.fresh_campaign_fixture=True
    if not 5 <= args.seconds <= 120:
        parser.error('--seconds must be between 5 and 120')
    exe = args.exe.resolve()
    if hashlib.sha256(exe.read_bytes()).hexdigest() != KNOWN_HASH:
        raise ValueError('Unknown executable build: refusing instrumentation')
    root = Path(__file__).resolve().parents[1] / '.runtime'
    sandbox = root / ('probe-user-' + str(time.time_ns()))
    sandbox.mkdir(parents=True)
    dll = root / 'RepopulatedDiagnostic.dll'
    if not dll.exists():
        raise ValueError('Build .runtime/RepopulatedDiagnostic.dll first')
    output = sandbox / 'native-telemetry.jsonl'
    env = dict(os.environ)
    env.update(USERPROFILE=str(sandbox), APPDATA=str(sandbox), LOCALAPPDATA=str(sandbox),
               REPOPULATED_DIAGNOSTIC_LOG=str(output))
    if args.test_control:
        env['REPOPULATED_TEST_CONTROL'] = '1'
    else:
        env.pop('REPOPULATED_TEST_CONTROL', None)
    config = {'sandbox': str(sandbox), 'dll': str(dll), 'traceFiles': bool(args.restore_level)}
    if args.campaign_remote_test:
        config.update(networkControl=True,directControl=True,ownedFactions=[20008],campaignRemote=True,activeShips={'20008':0x70000002})
    if args.cluster_export_test:
        config['exportCluster']=True
        env['REPOPULATED_CLUSTER_EXPORT']=str(sandbox / 'exported-cluster.lua')
    messages = []
    source = args.input.resolve() if args.input else exe.parent.parent / 'data' / 'ships' / '8_interceptor.lua'
    if args.fresh_campaign_fixture:
        if args.headless or args.sandbox_commands or args.sandbox_file:
            parser.error('Fresh campaign fixture cannot be combined with sandbox modes')
        create_campaign_fixture(sandbox,source)
        args.load_slot=0; config.update(trackPlayer=True)
    commands = args.sandbox_file.read_text() if args.sandbox_file else args.sandbox_commands
    factions = (7,8)
    if args.weapon_fire_test:
        if commands or args.restore_level or args.two_ship_sandbox or args.faction_control_test or args.network_control_test or args.test_control:
            parser.error('--weapon-fire-test requires its own scenario')
        args.headless=True
        config['fireTest']=True
        commands=f'import {source.as_posix()}; activate; sleep 10; echo finished'
    if args.same_content_factions and not (args.faction_control_test or args.network_control_test):
        parser.error('--same-content-factions requires a faction control test')
    if args.faction_control_test or args.network_control_test:
        if commands or args.restore_level or args.two_ship_sandbox or args.tournament or args.test_control:
            parser.error('--faction-control-test requires its own scenario')
        args.headless = True
        ships = exe.parent.parent / 'data' / 'ships'
        fixture = sandbox / 'fixture-ships'
        fixture.mkdir()
        for name, position in [('8_interceptor.lua', '-1500,0'), ('7_1.lua', '1500,0')]:
            text = (ships / name).read_text()
            (fixture / name).write_text(text.replace('{blocks={', '{position={' + position + '},blocks={', 1))
        if args.same_content_factions:
            for file in fixture.iterdir():
                file.unlink()
            factions = (10008,20008)
            template = (ships / '8_interceptor.lua').read_text()
            for faction in factions:
                modified = template.replace('{800, {-1.349, 0}}',
                    '{800, {-1.349, 0}, command={faction=' + str(faction) + '}}', 1)
                if modified == template:
                    raise ValueError('Stock ship command template changed')
                (fixture / f'8_empire_{faction}.lua').write_text(modified)
        commands = f'import {fixture.as_posix()}; activate; sleep 10; echo finished'
        config['moves'] = [
            {'ownerFaction':factions[0], 'targetFaction':factions[1], 'x':0, 'y':1000},
            {'ownerFaction':factions[1], 'targetFaction':factions[1], 'x':0, 'y':3000},
            {'ownerFaction':factions[0], 'targetFaction':factions[0], 'x':6000, 'y':3000}]
        if args.network_control_test:
            config.pop('moves')
            config['networkControl'] = True
    if args.restore_level:
        if commands or args.two_ship_sandbox or args.tournament:
            parser.error('--restore-level cannot be combined with another scenario')
        destination = sandbox / 'Reassembly' / 'checkpoint.lua.gz'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.restore_level.resolve(), destination)
        args.headless = True
        commands = f'level_load {destination.as_posix()}; sleep 1; echo finished'
    if args.two_ship_sandbox:
        if commands or args.tournament:
            parser.error('--two-ship-sandbox cannot be combined with custom scripts or tournament mode')
        args.headless = True
        ship = source.as_posix()
        if any(c in ship for c in ';\r\n'):
            parser.error('Ship path contains a sandbox command separator')
        commands = (f'cursor -1000 0 0; import {ship}; '
                    f'cursor 1000 0 0; import {ship}; activate; sleep 10; echo finished')
    device = frida.get_local_device()
    pid = None
    bridge = None
    network_results = []
    previous = Path.cwd()
    try:
        os.chdir(exe.parent)
        argv = [str(exe), 'kNetworkEnable=0']
        if args.load_slot is not None:
            argv.append('kLoadSlot='+str(args.load_slot))
        if args.headless:
            argv.append('kHeadlessMode=' + str(args.headless_mode))
            argv.append('kInputPath=' + json.dumps(str(source)))
        if args.tournament:
            argv += ['kTournamentMode=1', 'kTournamentHeadless=1', 'kTournamentSkipUnqualifiedShips=0']
        if commands:
            argv.append('kSandboxScript=' + json.dumps(commands))
        pid = device.spawn(argv, env=env, stdio='pipe')
        session = device.attach(pid)
        script = session.create_script(SCRIPT.replace('__CONFIG__', json.dumps(config)))
        def message(msg, data):
            record = msg.get('payload', msg)
            messages.append(record)
            print(json.dumps(record), flush=True)
        script.on('message', message)
        script.load()
        if args.network_control_test or args.campaign_remote_test:
            bridge = ControlBridge(lambda command: script.exports_sync.enqueue(command), factions)
        device.resume(pid)
        if bridge and not args.campaign_remote_test:
            network_results = bridge.exercise()
        if args.campaign_remote_test:
            deadline=time.monotonic()+5
            while not any(m.get('type')=='campaign-remote-spawn' for m in messages):
                if time.monotonic()>deadline: raise RuntimeError('Remote campaign spawn timed out')
                time.sleep(0.05)
            # Bind this connection to the second empire without trusting caller ownership.
            remote_token=next(token for token,faction in bridge.tokens.items() if faction==8)
            bridge.tokens[remote_token]=20008
            with socket.create_connection(bridge.server.server_address,timeout=3) as control_socket:
                with control_socket.makefile('rwb') as stream:
                    def request(command):
                        stream.write(json.dumps(command).encode()+b'\n');stream.flush()
                        return json.loads(stream.readline(4097))
                    if request({'token':remote_token}).get('faction')!=20008: raise RuntimeError('Remote campaign auth failed')
                    denied=request({'seq':0,'action':'drive','targetFaction':100,'x':1,'y':0})
                    if denied.get('code')!='NOT_OWNER': raise RuntimeError('Campaign cross-faction input accepted')
                    for seq in range(1,26):
                        if request({'seq':seq,'action':'drive','targetFaction':20008,'x':1,'y':0}).get('type')!='queued':
                            raise RuntimeError('Campaign drive denied')
                        time.sleep(0.1)
                    if request({'seq':26,'action':'fire','targetFaction':20008,'x':5000,'y':0}).get('type')!='queued':
                        raise RuntimeError('Campaign fire denied')
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        os.chdir(previous)
        if bridge:
            bridge.close()
        if pid:
            try:
                device.kill(pid)
            except frida.ProcessNotFoundError:
                pass
        (sandbox / 'instrumentation.json').write_text(json.dumps(messages, indent=2))
        print('Diagnostic artifacts:', sandbox, flush=True)
    errors = [m for m in messages if isinstance(m, dict) and m.get('type') == 'error']
    log_file=sandbox/'Reassembly'/'data'/'log_latest.txt'
    log_text=''
    for attempt in range(30):
        try:
            log_text=log_file.read_text(errors='replace') if log_file.exists() else ''
            break
        except PermissionError:
            if attempt==29: raise
            time.sleep(0.1)
    if '[SDL] Closing log: Crashed' in log_text or 'Code: EXCEPTION_' in log_text:
        raise RuntimeError('Isolated game process crashed; native validation failed')
    if errors or not output.exists():
        raise RuntimeError('Native sampling not established; inspect diagnostic artifacts')
    records = [json.loads(line) for line in output.read_text().splitlines()]
    zones = [r for r in records if r.get('type') == 'zone' and r.get('ships')]
    if not zones:
        raise RuntimeError('No populated zone observed')
    controls = [r for r in records if r.get('type') == 'control-verified']
    fired = [m for m in messages if isinstance(m,dict) and m.get('type')=='weapon-fire-result' and m.get('result',0)>0]
    weapon_entities = any(z.get('projectiles',0)>0 or z['clusters']>zones[0]['clusters'] for z in zones[1:])
    if args.cluster_export_test:
        exports=[m for m in messages if isinstance(m,dict) and m.get('type')=='cluster-export-result']
        if len(exports)!=1 or exports[0]['bytes']<=0 or not (sandbox / 'exported-cluster.lua').exists():
            raise RuntimeError('Native cluster serialization failed')
    if args.weapon_fire_test and (not fired or not weapon_entities):
        raise RuntimeError('Native weapon firing was not observed')
    campaign_player=[m for m in messages if m.get('type')=='native-player-update']
    if args.fresh_campaign_fixture and ('GSFly() pushed from GSContinueGame()' not in log_text or
        not campaign_player or campaign_player[-1]['calls']<300 or 'Missing player on continue' in log_text):
        raise RuntimeError('Fresh campaign player did not remain active')
    if args.campaign_remote_test:
        spawned=[m for m in messages if m.get('type')=='campaign-remote-spawn']
        drives=[m for m in messages if m.get('type')=='network-drive-result']
        shots=[m for m in messages if m.get('type')=='network-fire-result']
        if len(spawned)!=1 or spawned[0]['result']!=1 or len(drives)!=25 or any(m['result']!=1 for m in drives):
            raise RuntimeError('Remote campaign flight failed')
        if len(shots)!=1 or shots[0]['result']<=0 or not any(m.get('type')=='vanilla-ai-preserved' for m in messages):
            raise RuntimeError('Remote campaign firing or vanilla AI failed')
    if args.test_control and not controls:
        raise RuntimeError('Second command-ship control was not verified')
    initial = {ship['address']: (ship['x'], ship['y']) for ship in zones[0]['ships']}
    moving = sorted({ship['address'] for zone in zones[1:] for ship in zone['ships']
                     if ship['address'] in initial and
                     (ship['x'] - initial[ship['address']][0]) ** 2 +
                     (ship['y'] - initial[ship['address']][1]) ** 2 > 1})
    if args.faction_control_test or args.network_control_test:
        result_type = 'network-waypoint-result' if args.network_control_test else 'waypoint-result'
        results = [m for m in messages if isinstance(m, dict) and m.get('type') == result_type]
        expected_results = [1, 1] if args.network_control_test else [-1, 1, 1]
        if [r['result'] for r in results] != expected_results:
            raise RuntimeError('Native faction ownership or waypoint dispatch failed')
        first = {s['faction']: s for s in zones[0]['ships']}
        last = {s['faction']: s for s in zones[-1]['ships']}
        if not set(factions).issubset(first) or not set(factions).issubset(last):
            raise RuntimeError('Separate factions did not survive the fixture')
        if last[factions[1]]['x'] >= first[factions[1]]['x'] - 100 or last[factions[0]]['x'] <= first[factions[0]]['x'] + 100:
            raise RuntimeError('Factions did not move independently toward commanded waypoints')
    if args.two_ship_sandbox and (len(initial) < 2 or not moving):
        raise RuntimeError('Two-ship simulation did not demonstrate advancing positions')
    summary = {'schema': 1, 'gameHash': KNOWN_HASH, 'zoneSamples': len(zones),
               'movingEntitiesObserved': len(moving),
               'factionWaypointValidated': bool(args.faction_control_test or args.network_control_test),
               'networkWaypointValidated': bool(args.network_control_test), 'networkChecks': network_results,
               'sameContentEmpiresValidated': bool(args.same_content_factions),
               'nativeWeaponFireValidated': bool(args.weapon_fire_test and fired),
               'weaponEntitiesObserved': bool(args.weapon_fire_test and weapon_entities),
               'nativeClusterExportValidated': bool(args.cluster_export_test),
               'threads': sorted({z['thread'] for z in zones}), 'controlVerified': bool(controls),
               'control': controls, 'campaignValidated': False, 'multiplayerValidated': False,
               'campaignStartupValidated':bool(args.fresh_campaign_fixture),
               'campaignRemoteControlValidated':bool(args.campaign_remote_test),
               'mode': 'rendered-campaign-fixture' if args.fresh_campaign_fixture else
                       'rendered-sandbox' if args.headless and args.headless_mode==0 else
                       'headless-experiment' if args.headless else 'menu-demo',
               'artifactDirectory': str(sandbox)}
    (sandbox / 'summary.json').write_text(json.dumps(summary, indent=2))
    if args.report_file:
        args.report_file.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
