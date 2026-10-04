"""Private native test: remote discovery must change cells before save/rejoin.

This is not a gameplay command. On the owned host's update thread, temporarily
move its private remote fixture to an unexplored corner, invoke the production
discovery helper, and restore the original pose before another physics step.
"""
import json
import threading
import time
from campaign_map_wire import validate_map


SCRIPT=r'''
const folder=__FOLDER__;
const game=Process.getModuleByName('ReassemblyRelease.exe');
const dll=Process.getModuleByName('RepopulatedDiagnostic.dll');
const ident=new NativeFunction(dll.getExportByName('RepopulatedClusterIdent'),'uint',['pointer']);
const pose=new NativeFunction(dll.getExportByName('RepopulatedApplyPose'),'int',['pointer','int','uint','float','float','float','float','float']);
const discover=new NativeFunction(dll.getExportByName('RepopulatedExploreRemoteMap'),'int',['pointer']);
const hostMap=new NativeFunction(dll.getExportByName('RepopulatedExportCampaignMap'),'int',['pointer','pointer']);
const exportMap=new NativeFunction(dll.getExportByName('RepopulatedExportRemoteCampaignMap'),'int',['pointer','pointer']);
let requested=false,complete=false;
rpc.exports={request(){requested=true;}};
Interceptor.attach(game.getExportByName('?Update@GameZone@@QEAAXXZ'),{
  onEnter(args){this.zone=args[0];},onLeave(){
    if(!requested || complete)return;
    complete=true;const zone=this.zone;
    const begin=zone.add(0x188).readPointer(),count=zone.add(0x190).readPointer().sub(begin).toInt32()/8;
    if(count<0 || count>4096)throw new Error('Map probe root bound differs');
    const matches=[];
    for(let i=0;i<count;i++){
      const ship=begin.add(i*8).readPointer();
      if(ident(ship)===0x70000002 && ship.add(0x178).readPointer().isNull() && ship.add(0x118).readS32()===20008)matches.push(ship);
    }
    if(matches.length!==1)throw new Error('Map probe requires one private remote ship');
    const ship=matches[0],original=[ship.add(0x30).readDouble(),ship.add(0x38).readDouble(),ship.add(0x40).readDouble(),ship.add(0x48).readDouble(),ship.add(0x60).readFloat()];
    const before=folder+'/probe-map-before.json',after=folder+'/probe-map-after.json';
    if(hostMap(zone,Memory.allocUtf8String(folder+'/probe-host-map-before.json'))<0)throw new Error('Host map baseline export failed');
    if(exportMap(zone,Memory.allocUtf8String(before))<0)throw new Error('Map probe baseline export failed');
    let marked;
    try{
      if(pose(zone,20008,0x70000002,24000,24000,0,0,original[4])!==1)throw new Error('Map probe move failed');
      marked=discover(zone);
      if(marked<0 || exportMap(zone,Memory.allocUtf8String(after))<0)throw new Error('Map probe discovery failed');
      if(hostMap(zone,Memory.allocUtf8String(folder+'/probe-host-map-after.json'))<0)throw new Error('Host map comparison export failed');
    }finally{
      if(pose(zone,20008,0x70000002,...original)!==1)throw new Error('Map probe pose restore failed');
    }
    send({type:'native-map-discovery-probe',marked,before,after,thread:Process.getCurrentThreadId(),nativeDiscoveryRadius:2*game.base.add(0x3cf3e8).readFloat()});
  }
});
'''


def check_remote_discovery(host):
    if host.peer is not None:raise ValueError('Discovery probe requires an unoccupied private test host')
    deadline=time.monotonic()+15
    while not host.latest and not host.failures and time.monotonic()<deadline:time.sleep(.05)
    if not host.latest or host.failures:raise RuntimeError('Discovery probe host failed to start')
    done=threading.Event();records=[]
    script=host.game.session.create_script(SCRIPT.replace('__FOLDER__',json.dumps(str(host.game.folder))))
    def message(raw,data):
        record=raw.get('payload',raw)
        if record.get('type') in ('error','native-map-discovery-probe'):records.append(record);done.set()
    script.on('message',message)
    try:
        script.load();script.exports_sync.request()
        if not done.wait(15):raise RuntimeError('Discovery probe timed out')
        record=records[-1]
        if record.get('type')=='error':raise RuntimeError(record.get('description','Native discovery probe failed'))
        before=validate_map(json.loads((host.game.folder/'probe-map-before.json').read_text()))
        after=validate_map(json.loads((host.game.folder/'probe-map-after.json').read_text()))
        old=sum(row[2] for row in before['cells']);new=sum(row[2] for row in after['cells'])
        if new<=old or any(left[2] and not right[2] for left,right in zip(before['cells'],after['cells'])):
            raise RuntimeError('Remote native discovery did not reveal additional map cells')
        host_before=validate_map(json.loads((host.game.folder/'probe-host-map-before.json').read_text()))
        host_after=validate_map(json.loads((host.game.folder/'probe-host-map-after.json').read_text()))
        host_unchanged=[r[2] for r in host_before['cells']]==[r[2] for r in host_after['cells']]
        if not host.shared_exploration and not host_unchanged:raise RuntimeError('Private remote discovery revealed host map cells')
        if host.shared_exploration and [r[2] for r in host_after['cells']]!=[r[2] for r in after['cells']]:raise RuntimeError('Enabled shared exploration did not produce matching discovery')
        return {'nativeRemoteExplorationValidated':True,'initialExploredCells':old,'exploredCellsAfterRemoteDiscovery':new,'nativeUpdateThread':record['thread'],
                'sharedExploration':host.shared_exploration,'hostExplorationUnchanged':host_unchanged,'nativeDiscoveryRadius':record['nativeDiscoveryRadius']}
    finally:script.unload()
