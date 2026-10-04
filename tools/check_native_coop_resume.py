"""Save, terminate, reload and rejoin the native two-player alpha."""
import argparse
import json
import math
from pathlib import Path
import time
from native_coop import CoopHost,run_client
from native_checkpoints import Checkpoints
from native_map_probe import check_remote_discovery
from scene_codec import geometry,Reader
from campaign_map_wire import load_map_settings,visible_map


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--shared-exploration',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]/'.runtime'/('resume-check-'+str(time.time_ns()))
    root.mkdir();store=Checkpoints(root/'checkpoints');host=None
    try:
        host=CoopHost(args.exe,root/'first-host',('127.0.0.1',0),shared_exploration=args.shared_exploration)
        discovery=check_remote_discovery(host)
        address=('127.0.0.1',host.server.server_address[1])
        first=run_client(args.exe,root/'first-client',address,host.token,seconds=10,test_controls=True)
        (root/'first-session.json').write_text(json.dumps(dict(first,hostFailures=host.failures),indent=2))
        if first['failures'] or host.failures:raise RuntimeError('Initial session failed: '+str(first['failures']+host.failures))
        manifest=host.checkpoint(store,'Resume regression');source,_=store.load(manifest['id'],host.content)
        expected=geometry((source/'player-ships.lua').read_bytes())
        expected_map=json.loads((source/'campaign-map.json').read_text())
        expected_remote_map=visible_map(json.loads((source/'remote-map.json').read_text()))
        if load_map_settings(source)['sharedExploration']!=args.shared_exploration:raise RuntimeError('Saved sharing setting differs')
        bounds=Reader((source/'save.lua').read_bytes()).value()['fields']['mapTotalSize']['items']
        if len(bounds)!=2 or any(not math.isfinite(v) or v<=0 for v in bounds):raise RuntimeError('Invalid saved galaxy bounds')
        host.close();host=None
        host=CoopHost(args.exe,root/'resumed-host',('127.0.0.1',0),campaign_source=source)
        deadline=time.monotonic()+15
        while not host.latest and not host.failures and time.monotonic()<deadline:time.sleep(0.1)
        if not host.latest or host.failures:raise RuntimeError('Resumed host failed')
        restored_map=host.latest_host_map
        if restored_map['width']!=expected_map['width'] or restored_map['radius']!=expected_map['radius'] or any(old[2] and not new[2] for old,new in zip(expected_map['cells'],restored_map['cells'])):
            raise RuntimeError('Saved exploration was lost')
        if not any(m.get('type')=='campaign-map-restored' for m in host.game.messages):raise RuntimeError('Saved authoritative map was not restored')
        if host.shared_exploration!=args.shared_exploration:raise RuntimeError('Saved sharing setting was not restored')
        if not any(m.get('type')=='remote-map-restored' for m in host.game.messages):raise RuntimeError('Saved remote map was not restored')
        if any(old[2] and not new[2] for old,new in zip(expected_remote_map['cells'],host.latest['map']['cells'])):raise RuntimeError('Saved remote discovery was lost')
        observed=geometry((host.game.folder/'loaded-before-continue.lua').read_bytes())
        expected_health={entry['ident']:entry['values'] for entry in json.loads((source/'player-health.json').read_text())}
        restored=next(m for m in host.game.messages if m.get('type')=='campaign-restore-scene')
        observed_health={entry['ident']:entry['values'] for entry in restored['playerHealth']}
        comparisons=[]
        for ident in (0x70000001,0x70000002):
            left=expected.get(ident);right=observed.get(ident)
            if not left or not right or left['faction']!=right['faction'] or len(left['blocks'])!=len(right['blocks']):
                raise RuntimeError('Saved faction ship geometry or ownership differs')
            geometry_error=0
            for before,after in zip(sorted(left['blocks']),sorted(right['blocks'])):
                if before[0]!=after[0]:raise RuntimeError('Saved block type differs')
                geometry_error=max(geometry_error,*(abs(before[i]-after[i]) for i in (1,2,3)))
            if geometry_error>0.01:raise RuntimeError('Saved block geometry differs')
            health_before=expected_health[ident];health_after=observed_health[ident]
            if len(health_before)!=len(health_after):raise RuntimeError('Saved health block count differs')
            health_error=max(abs(a-b) for a,b in zip(health_before,health_after))
            if health_error>0.1:raise RuntimeError('Saved runtime damage differs: '+str(health_error))
            delta=[left['pose'][i]-right['pose'][i] for i in (0,1)]
            # Native sectors restore positions into the wrapped galaxy range.
            distance=math.hypot(*(math.remainder(delta[i],bounds[i]) for i in (0,1)))
            if distance>0.2:raise RuntimeError('Saved ship position differs: '+str(distance))
            if any(abs(left['pose'][i]-right['pose'][i])>0.1 for i in (2,3)) or abs(math.remainder(left['pose'][4]-right['pose'][4],2*math.pi))>0.1:
                raise RuntimeError('Saved ship velocity or angle differs')
            comparisons.append({'ident':ident,'faction':left['faction'],'positionError':distance,'rawPositionDelta':delta,'geometryError':geometry_error,'healthError':health_error,'blocks':len(left['blocks'])})
        spawn=[m for m in host.game.messages if m.get('type') in ('campaign-fixture-spawn','campaign-remote-spawn')]
        if len(spawn)!=2 or any(m['result']!=2 for m in spawn):raise RuntimeError('Saved player ships were recreated')
        second=run_client(args.exe,root/'resumed-client',('127.0.0.1',host.server.server_address[1]),host.token,seconds=10,test_controls=True)
        if second['failures'] or host.failures or second['updates']<3:raise RuntimeError('Rejoin failed')
        result={'nativeAlphaSaveResumeValidated':True,'fullCampaignMultiplayerValidated':False,
                'checkpoint':manifest['id'],'ships':comparisons,'firstSession':first,'resumedSession':second,
                'nativeMapSaveResumeValidated':True,'savedExploredCells':sum(row[2] for row in expected_map['cells']),
                'remoteDiscovery':discovery,'sharedExploration':host.shared_exploration,
                'savedRemoteExploredCells':sum(row[2] for row in expected_remote_map['cells'])}
        (root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    finally:
        if host:host.close()


if __name__=='__main__':main()
