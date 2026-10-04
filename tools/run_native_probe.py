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
let replicaUpdater=null,replicaRemover=null,replicaStats=null;
let campaignMapExporter=null,campaignMapRemoteExporter=null,campaignMapApply=null,campaignMapExplore=null,campaignObjectivesApply=null,nativeMapRenders=0;
let replicaInitialized=false;
let campaignAuthorityZone=null,campaignAuthorityEnded=false,hostFlightState=null,hostMenuActive=false,hostMenuBusy=false,hostUpdateThread=null,hostMenuUpdates=0,hostMenuBrakes=0;
const campaignConsole=cfg.nativeCampaignClient?Memory.alloc(0x300):null;
let campaignReplicaZone=null;
const nativeWeaponTargets=new Map();
let nativeFocused=true,nativeFlightActive=true,lastNativeIntent=null;
let testNativeMenuStage=0,testNativeMenuAt=0,testNativeModal=null;
let nativeFlightState=null,nativeUIHeartbeat=null,applyingFromMenu=false,menuSnapshotApplies=0,heartbeatBusy=false,nativeUpdateThread=null;
function sendNativeIntent(record) {
  lastNativeIntent=record;
  if(!nativeFocused || !nativeFlightActive || openEditors.size)record={...record,dimensions:0x10a,destination:[0,0,0,0,0,0],
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
function packedBuffer(hex){
  const data=[];for(let i=0;i<hex.length;i+=2)data.push(parseInt(hex.slice(i,i+2),16));
  const buffer=Memory.alloc(Math.max(1,data.length));if(data.length)buffer.writeByteArray(data);return buffer;
}
let interestExporter=null,displayFrames=0,lastPilotInput=0,drawCalls=0,pollCalls=0,lastView=null,replicaPilot=null,pilotRenderCalls=0;
let lastPresentation=0,longFrames=0,maxFrameMs=0;
let replicaPresenter=null,replicaPresentedAt=0,predictedFrames=0;
let visualExporter=null,visualApply=null,visualTick=null,visualProjectiles=null,visualStats=null,visualIdentities=null;
let projectilePass1Calls=0;
const presentedPilot=Memory.alloc(8);
const frameHistogram=new Array(251).fill(0);
const pilotKeys=new Set();
let mouseOffset=null,mouseHeld=false,mouseX=0,mouseY=0,mouseWindow=null,getWindow=null,getWindowSize=null;
let textInputActive=null;
const openEditors=new Set();
if(cfg.nativeCampaignClient){
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
        send({type:'native-menu-test',action:'opened',tab,displayFrames,nativeMapRenders,menuSnapshotApplies,thread:Process.getCurrentThreadId(),nativeUpdateThread});
      }else if([1,3].includes(testNativeMenuStage) && Date.now()-testNativeMenuAt>=2000){
        const list=testNativeModal.add(0x10).readPointer();
        const current=list.add(8).readPointer().add(list.add(0x70).readS32()*8).readPointer();
        if(!current.equals(testNativeModal))throw new Error('Native menu test lost active modal');
        new NativeFunction(game.base.add(0x118070),'void',['pointer','pointer','float'])(list,testNativeModal,0.5);
        send({type:'native-menu-test',action:'closed',tab:testNativeMenuStage===1?1:0x10,displayFrames,nativeMapRenders,menuSnapshotApplies,thread:Process.getCurrentThreadId(),nativeUpdateThread});
        testNativeModal=null;testNativeMenuStage++;
      }
    }
    const active=topState(state);if(active!==nativeFlightActive){nativeFlightActive=active;if(lastNativeIntent)sendNativeIntent(lastNativeIntent);}
    if(!active && campaignReplicaZone){
      if(Process.getCurrentThreadId()!==nativeUpdateThread)throw new Error('Native UI update thread differs from replica update thread');
      applyingFromMenu=true;try{sample(campaignReplicaZone);}finally{applyingFromMenu=false;}
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
    drawCalls++;
    if(visualTick && visualTick(args[0])<0) throw new Error('Native visual effect replay failed');
    if(replicaPresenter && replicaPresentedAt) {
      const predicted=replicaPresenter(args[0],Math.min(0.25,Math.max(0,(Date.now()-replicaPresentedAt)/1000)),cfg.followPilot,presentedPilot);
      if(predicted<0) throw new Error('Replica presentation failed: '+predicted);
      if(predicted>0) {replicaPilot=[presentedPilot.readFloat(),presentedPilot.add(4).readFloat()];predictedFrames++;}
    }
    if(cfg.followPilot && replicaPilot) {
      // View offsets are checked against DrawGame's current machine code and
      // live viewport values; this is the replica camera, never host state.
      args[1].add(0x10).writeFloat(replicaPilot[0]);
      args[1].add(0x14).writeFloat(replicaPilot[1]);
      if(!cfg.nativeCampaignClient)args[1].add(0x20).writeFloat(2);
    }
    if(drawCalls%60===1) lastView=Array.from({length:18},(_,i)=>args[1].add(i*4).readFloat());
    if(sdlHooksInstalled) return;
    sdlHooksInstalled=true;
    const sdl=Process.getModuleByName('SDL2.dll');
    getWindow=new NativeFunction(sdl.getExportByName('SDL_GetWindowFromID'),'pointer',['uint']);
    getWindowSize=new NativeFunction(sdl.getExportByName('SDL_GetWindowSize'),'void',['pointer','pointer','pointer']);
    textInputActive=new NativeFunction(sdl.getExportByName('SDL_IsTextInputActive'),'int',[]);
    const pace=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPaceFrame'),'void',[]);
    Interceptor.attach(sdl.getExportByName('SDL_GL_SwapWindow'),{onEnter(){
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
  }});
  if(cfg.measureRendering) Interceptor.attach(game.getExportByName('?render@Block@@QEBAXPEAU?$MeshPair@UVertexPos2ColorTime@@UVertexPosColor@@@@@Z'),{
    onEnter(args) {
      if(!clusterIdent) return;
      const cluster=args[0].add(0xb8).readPointer();
      if(!cluster.isNull() && clusterIdent(cluster)===cfg.followPilot) pilotRenderCalls++;
    }
  });
}
let damageFixture=null,damagedFixture=false;
let partialDamageFixture=null,partiallyDamagedFixture=false;
let healthReader=null;
let driver=null,aimDriver=null,lastRemoteRespawn=0,remoteDestroyed=false;
let nativeDriver=null,nativeWeapons=null;
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
rpc.exports = { enqueue(command) {
  if (!cfg.networkControl) throw new Error('Network controls unavailable');
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
  testHoldStarted:controlHoldStarted,testHoldCompleted:controlHoldStarted && Date.now()>=holdControlsUntil};}, setremotecontrol(enabled) {
  if(!cfg.vacantRemoteAI || typeof enabled!=='boolean') throw new Error('Remote ownership unavailable');
  remoteControlEnabled=enabled;pendingMoves.length=0;driveStates.clear();fireStates.clear();
  send({type:'remote-ownership',playerControlled:enabled});return true;
}, checkpoint(request) {
  if(!cfg.campaignCheckpoints || pendingCheckpoint || typeof request.id!=='string' || !/^[a-f0-9]{32}$/.test(request.id) ||
     typeof request.path!=='string' || request.path.includes('..') ||
     request.path.replaceAll('\\','/')!==cfg.sandbox.replaceAll('\\','/')+'/checkpoint-'+request.id) throw new Error('Invalid private checkpoint request');
  pendingCheckpoint=request;return true;
}, applyworld(world) {
  if(!cfg.worldReplica || !Number.isSafeInteger(world.seq) || world.seq<=worldSeq ||
     typeof world.path!=='string' || world.path.includes('..') ||
     !world.path.replaceAll('\\','/').startsWith(cfg.sandbox.replaceAll('\\','/')+'/')) throw new Error('Invalid world update');
  if(cfg.persistentReplica){
    const plan=world.incremental;
    if(!plan || typeof world.hasAdditions!=='boolean' || !Array.isArray(plan.remove) || plan.remove.length>4096 ||
       new Set(plan.remove).size!==plan.remove.length || plan.remove.some(id=>!Number.isInteger(id)||id<=0||id>0xffffffff))throw new Error('Invalid incremental removals');
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
  worldSeq=world.seq; pendingWorld=world; return true;
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
const kernel = Process.getModuleByName('KERNEL32.dll');
if(cfg.worldReplica) Interceptor.attach(game.base.add(0xcab60), {
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
        command.nativeWeaponBuffer=Memory.alloc(Math.max(1,command.weapons.length*28));
        command.weapons.forEach((row,i)=>row.forEach((v,j)=>{const cell=command.nativeWeaponBuffer.add(i*28+j*4);if(j<2)cell.writeU32(v);else cell.writeFloat(v);}));
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
      sampler = new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedSample'), 'int', ['pointer']);
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
        replicaStats=new NativeFunction(dll.getExportByName('RepopulatedReplicaStats'),'void',['pointer']);
      }
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
        const effect=game.base.add(0x1cc440),prefix=[0x48,0x8b,0xc4,0x48,0x89,0x58,0x08,0x55,0x48,0x8d,0x68,0xc1];
        const actual=new Uint8Array(effect.readByteArray(prefix.length));
        if(prefix.some((b,i)=>actual[i]!==b)) throw new Error('Native thrust signature differs');
        const original=Interceptor.replaceFast(effect,module.getExportByName('RepopulatedCaptureThrust'));
        new NativeFunction(module.getExportByName('RepopulatedSetThrustOriginal'),'void',['pointer'])(original);
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
      loaded = true; send({type:'sampler-loaded'});
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
      if(visualIdentities && visualIdentities(zone)<0)throw new Error('Native visual identity assignment failed');
      const path=cfg.sandbox+'/world-'+(cfg.rollingWorld?sampled%16:sampled)+'.lua';
      let roots=interestExporter?interestExporter(zone,Memory.allocUtf8String(path),cfg.interestIdent,cfg.interestRadius || 3000):worldExporter(zone,Memory.allocUtf8String(path));
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
        roots=interestExporter(zone,Memory.allocUtf8String(path),cfg.interestIdent,cfg.interestRadius || 3000);
      }
      let visualPath=null;
      if(visualExporter && roots>=0) {
        visualPath=cfg.sandbox+'/visual-'+sampled%16+'.json';
        const visuals=visualExporter(zone,Memory.allocUtf8String(visualPath),cfg.interestIdent,cfg.interestRadius || 2000);
        if(visuals<0) {
          const message=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedPresentationMessage'),'pointer',[])().readUtf8String();
          throw new Error('Native presentation export failed: '+visuals+' '+message);
        }
      }
      let mapPath=null,remoteMapPath=null;
      if(campaignMapExporter && roots>=0){
        const explored=campaignMapExplore(zone);if(explored<0)throw new Error('Remote map exploration failed: '+explored);
        mapPath=cfg.sandbox+'/map-'+sampled%16+'.json';
        const cells=campaignMapExporter(zone,Memory.allocUtf8String(mapPath));if(cells<0)throw new Error('Campaign map export failed: '+cells);
        remoteMapPath=cfg.sandbox+'/remote-map-'+sampled%16+'.json';
        if(campaignMapRemoteExporter(zone,Memory.allocUtf8String(remoteMapPath))!==cells)throw new Error('Remote faction map export failed');
      }
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
      if(applyingFromMenu)menuSnapshotApplies++;
      const world=pendingWorld; pendingWorld=null;
      let clusters;
      let persistentReplica={};
      if(cfg.persistentReplica){
        for(const ident of world.incremental.remove){const removed=replicaRemover(zone,ident);if(removed!==1)throw new Error('Replica removal failed: '+removed);}
        if(world.hasAdditions){
          const streamer=zone.add(0x248).readPointer();
          // The verified level helper can own a standalone field. A replica
          // must not inject network ships into the local sector streamer's cache.
          if(cfg.nativeCampaignClient)zone.add(0x248).writePointer(ptr(0));
          try{clusters=worldLoader(zone,consoleContext,Memory.allocUtf8String(world.path));}
          finally{if(cfg.nativeCampaignClient)zone.add(0x248).writePointer(streamer);}
        }else clusters=zone.add(0x190).readPointer().sub(zone.add(0x188).readPointer()).toInt32()/8;
        if(clusters>=0){
          const plan=world.incremental;
          const updated=replicaUpdater(zone,packedBuffer(plan.poses),plan.poses.length/80,packedBuffer(plan.health),plan.health.length/32);
          if(updated<0)throw new Error('Persistent replica update failed: '+updated);
          const stats=Memory.alloc(24);replicaStats(stats);
          persistentReplica={retained:plan.retained,replaced:plan.replaced,totalPoseUpdates:stats.readU64().toNumber(),
            totalRemovals:stats.add(8).readU64().toNumber(),totalHealthUpdates:stats.add(16).readU64().toNumber()};
        }
      }else clusters=worldLoader(zone,consoleContext,Memory.allocUtf8String(world.path));
      let presentation={};
      if(visualApply && clusters>=0) {
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
      replicaPresentedAt=Date.now();
      if(cfg.nativeCampaignClient && clusters>=0){
        const bind=new NativeFunction(Process.getModuleByName('RepopulatedDiagnostic.dll').getExportByName('RepopulatedBindReplicaPlayer'),'int',['pointer','uint']);
        const bound=bind(zone,cfg.followPilot);if(bound!==1)throw new Error('Native replica player binding failed: '+bound);
      }
      let campaignMap={};
      if(campaignMapApply && clusters>=0){
        const map=world.map;
        const cells=campaignMapApply(zone,packedBuffer(map.radius),map.width,packedBuffer(map.cells),map.width*map.width,packedBuffer(map.regions),map.regions.length/24);
        if(cells<0)throw new Error('Native campaign map apply failed: '+cells);
        const objectives=campaignObjectivesApply(zone,packedBuffer(map.objectives),map.objectives.length/72);
        if(objectives<0)throw new Error('Native campaign objectives apply failed: '+objectives);
        const verify=cfg.sandbox+'/verified-map-'+world.seq+'.json';
        if(campaignMapExporter(zone,Memory.allocUtf8String(verify))!==cells)throw new Error('Native campaign map readback failed');
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
      const verifiedPath=cfg.sandbox+'/verified-world-'+world.seq+'.lua';
      const verifiedRoots=clusters>=0?worldExporter(zone,Memory.allocUtf8String(verifiedPath)):-1;
      send({type:'world-applied',seq:world.seq,clusters,expectedRoots:world.roots,
        verifiedPath,verifiedRoots,inputSha256:world.sha256,message:worldLoadMessage().readUtf8String(),
        displayFrames,drawCalls,pollCalls,lastView,pilotRenderCalls,replicaPilot,
        frameTiming:{histogram:frameHistogram,longFrames,maxFrameMs},predictedFrames,presentation,persistentReplica,pilotPointer,campaignMap,
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
function beforeZoneUpdate(zone) {
    if(campaignAuthorityEnded)return;
    if(cfg.campaignRemote && campaignAuthorityZone && !zone.equals(campaignAuthorityZone))return;
    if(cfg.keepHostRunningInMenus)hostUpdateThread=Process.getCurrentThreadId();
    if ((cfg.networkControl || cfg.replica) && !cfg.frameLimit) Thread.sleep(1/60);
    if(cfg.nativeCampaignClient && campaignReplicaZone && zone.equals(campaignReplicaZone))nativeUpdateThread=Process.getCurrentThreadId();
    if(cfg.nativeCampaignClient && campaignReplicaZone && zone.equals(campaignReplicaZone)){campaignConsole.add(8).writePointer(zone);consoleContext=campaignConsole;}
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
                const result=nativeWeapons(cluster.add(8).readPointer(),faction,pilot,state.nativeWeaponBuffer,state.weapons.length,Number(Date.now()-state.received>500));
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
    aiForward(ai,force);vanillaCalls++;
    if(vanillaCalls===300) send({type:'vanilla-ai-preserved',calls:vanillaCalls});
  },'void',['pointer','bool']);
  const original=Interceptor.replaceFast(game.getExportByName('?update@AI@@QEAAX_N@Z'),aiOverride);
  aiForward=new NativeFunction(original,'void',['pointer','bool']);
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
  let lastNativeIntent=0;
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
      if(cfg.captureNativeIntent && Date.now()-lastNativeIntent>=100) {
        lastNativeIntent=Date.now();const ai=this.ai;
        const command=ai.add(0x278).readPointer(),cluster=command.add(0xb8).readPointer();
        const destination=Array.from({length:6},(_,i)=>ai.add(0x2bc+i*4).readFloat());
        const precision=Array.from({length:4},(_,i)=>ai.add(0x2a8+i*4).readFloat());
        const weapons=[],weaponFeatures=[];
        if(cfg.nativeCampaignClient){
          destination[0]-=cluster.add(0x30).readDouble();destination[1]-=cluster.add(0x38).readDouble();
          const begin=cluster.add(0xf0).readPointer(),end=cluster.add(0xf8).readPointer(),count=end.sub(begin).toInt32()/8;
          if(count<0 || count>4096)throw new Error('Native control block limit');
          for(let i=0;i<count;i++){
            const block=begin.add(i*8).readPointer(),features=block.add(0x40).readU64().and(uint64('0x800008e0')).toNumber();
            if(!features)continue;
            const id=block.add(0x30).readU32(),enabled=block.add(0x100).readU64().and(uint64('0x800008e0')).toNumber();
            const target=nativeWeaponTargets.get(id);
            weapons.push([id,target?enabled:0,...(target || [0,0,0,0,0])]);
            weaponFeatures.push(features);
          }
          if(weapons.length>256)throw new Error('Native weapon control limit');
          const alive=new Set(weapons.map(row=>row[0]));for(const id of nativeWeaponTargets.keys())if(!alive.has(id))nativeWeaponTargets.delete(id);
        }
        sendNativeIntent({type:'native-navigation-intent',ident:clusterIdent(cluster),dimensions:ai.add(0x2b8).readU32(),destination,precision,weapons,weaponFeatures,
              vx:ai.add(0x2c4).readFloat(),vy:ai.add(0x2c8).readFloat(),angle:ai.add(0x2cc).readFloat()});
      }
    }
  });
}
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
