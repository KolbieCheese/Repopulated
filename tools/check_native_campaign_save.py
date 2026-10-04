"""Verify native SaveGame writes in a fresh isolated campaign; no resume claim."""
import argparse
import gzip
import json
from pathlib import Path
import time
from native_session import NativeSession
from scene_codec import Reader


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,required=True)
    args=parser.parse_args();folder=Path(__file__).resolve().parents[1]/'.runtime'/('campaign-save-'+str(time.time_ns()))
    failures=[];events=[];game=None
    def message(record):
        if record.get('type')=='error':failures.append(record.get('description','Native error'))
        if record.get('type')=='campaign-native-save':events.append(record)
    try:
        game=NativeSession(args.exe,folder,{'networkControl':True,'directControl':True,'ownedFactions':[20008],
                           'activeShips':{'20008':0x70000002},'campaignRemote':True,'remoteOffset':[500,0],
                           'vacantRemoteAI':True,'campaignSaveProbe':True,'sampleIntervalMs':500,'maxSamples':40},message,campaign=True)
        game.resume();deadline=time.monotonic()+20
        while time.monotonic()<deadline and not events and not failures:time.sleep(0.1)
        if failures or not events or not events[0]['save'] or not events[0]['blueprints']:
            raise RuntimeError('Native save write failed: '+str(failures or events))
        saves=folder/'Reassembly'/'data'/'save0';files=[];metadata={}
        for name in ('save.lua','blueprints.lua'):
            path=saves/(name+'.gz')
            if not path.exists():path=saves/name
            if not path.exists():raise RuntimeError('Native save missing: '+str(path))
            raw=gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_bytes()
            root=Reader(raw);fields=root.value()['fields']
            if root.peek() is not None:raise RuntimeError('Trailing save data')
            if name=='save.lua' and fields.get('playerIdent')!=0x70000001:raise RuntimeError('Native save lost player identity')
            if name=='blueprints.lua' and not fields.get('blueprints'):raise RuntimeError('Native blueprint library missing')
            files.append({'file':path.name,'storedBytes':path.stat().st_size,'decodedBytes':len(raw)})
            if name=='save.lua':metadata={key:fields.get(key) for key in ('version','faction','playerIdent','points','credits','progress','controlScheme')}
        result={'nativeSaveWriteValidated':True,'campaignResumeValidated':False,'files':files,'metadata':metadata,'failures':failures}
        game.close();game=None
        resumed_events=[]
        def resumed_message(record):
            if record.get('type')=='error':failures.append(record.get('description','Native error'))
            if record.get('type') in ('campaign-fixture-spawn','campaign-remote-spawn','native-player-update'):
                resumed_events.append(record)
        game=NativeSession(args.exe,folder.with_name(folder.name+'-resumed'),
             {'networkControl':True,'directControl':True,'ownedFactions':[20008],'activeShips':{'20008':0x70000002},
              'campaignRemote':True,'remoteOffset':[500,0],'vacantRemoteAI':True,'sampleIntervalMs':500,'maxSamples':30},
             resumed_message,campaign=True,campaign_source=saves)
        game.resume();deadline=time.monotonic()+15
        while time.monotonic()<deadline and not failures and not any(e.get('type')=='native-player-update' for e in resumed_events):time.sleep(0.1)
        result['resumeEvents']=resumed_events
        result['nativeHostContinuationObserved']=any(e.get('type')=='native-player-update' for e in resumed_events)
        result['remoteShipPersisted']=any(e.get('type')=='campaign-remote-spawn' and e.get('result')==2 for e in resumed_events)
        if failures or not result['nativeHostContinuationObserved']:raise RuntimeError('Private host continuation failed: '+str(failures))
        (folder/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    finally:
        if game:game.close()


if __name__=='__main__':main()
