"""Verify authenticated direct flight through native thrusters, not pose setters."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from check_native_replication import Session
from research_control import ControlBridge
from run_native_probe import KNOWN_HASH


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    args=parser.parse_args(); exe=args.exe.resolve()
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=KNOWN_HASH:
        raise ValueError('Unknown game build')
    folder=Path(__file__).resolve().parents[1]/'.runtime'/('drive-check-'+str(time.time_ns()))
    fixture=folder/'fixture'; fixture.mkdir(parents=True)
    for name in ['8_interceptor.lua','7_1.lua']:
        (fixture/name).write_bytes((exe.parent.parent/'data'/'ships'/name).read_bytes())
    host=None; bridge=None; connections=[]; previous=Path.cwd()
    try:
        os.chdir(exe.parent)
        host=Session(exe,folder/'host',fixture,False,lambda message:None,direct_control=True)
        bridge=ControlBridge(lambda command:host.script.exports_sync.enqueue(command))
        host.resume()
        deadline=time.monotonic()+3
        while not any(m.get('type')=='bootstrap-exported' for m in host.messages):
            if time.monotonic()>deadline: raise RuntimeError('Native drive bootstrap timed out')
            time.sleep(0.05)
        for token,faction in bridge.tokens.items():
            sock=socket.create_connection(bridge.server.server_address,timeout=3)
            stream=sock.makefile('rwb')
            stream.write(json.dumps({'token':token}).encode()+b'\n'); stream.flush()
            if json.loads(stream.readline())['faction']!=faction: raise RuntimeError('Drive authentication failed')
            connections.append((sock,stream,faction))
        # Frame count is bounded by the research bridge's 32-command connection.
        for seq in range(25):
            for sock,stream,faction in connections:
                command={'seq':seq,'action':'drive','targetFaction':faction,'x':1 if faction==7 else -1,'y':0}
                stream.write(json.dumps(command).encode()+b'\n'); stream.flush()
                if json.loads(stream.readline()).get('type')!='queued': raise RuntimeError('Native drive command denied')
            time.sleep(0.1)
        # Stop transmitting. Native input must expire and command braking.
        time.sleep(2)
        errors=[m for m in host.messages if m.get('type')=='error']
        controls=[m for m in host.messages if m.get('type')=='network-drive-result']
        states=[m for m in host.messages if m.get('type')=='authoritative-state']
        expired={m['faction'] for m in host.messages if m.get('type')=='drive-expired'}
        if errors or not controls or any(m['result']!=1 for m in controls) or expired!={7,8}:
            raise RuntimeError('Direct drive or expiration failed: '+str(errors)+str(controls[-2:])+str(expired))
        movement={}
        for faction in [7,8]:
            ships=[next(s for s in m['ships'] if s['faction']==faction) for m in states]
            distance=max(s['x'] for s in ships)-min(s['x'] for s in ships)
            velocities=[s['vx'] for s in ships]
            if distance<20 or (max(velocities)<10 if faction==7 else min(velocities)>-10):
                raise RuntimeError('Native thrusters did not move the owned ship')
            movement[faction]={'distance':distance,'velocities':velocities}
        result={'schema':1,'nativeThrusterDriveValidated':True,'staleInputBrakingCommandValidated':True,
                'multiplayerValidated':False,'campaignValidated':False,'movement':movement,'commandsApplied':len(controls)}
        (folder/'result.json').write_text(json.dumps(result,indent=2))
        print('Native direct-flight check passed. Evidence:',folder/'result.json')
    finally:
        for sock,stream,_ in connections:
            stream.close(); sock.close()
        if bridge: bridge.close()
        if host: host.close()
        os.chdir(previous)


if __name__=='__main__': main()
