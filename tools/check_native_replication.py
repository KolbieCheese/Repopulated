"""Two-process TCP ship bootstrap and pose fixture, not full multiplayer."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import sys
import threading
import time
from run_native_probe import frida, SCRIPT, KNOWN_HASH
from research_control import ControlBridge
from scene_codec import compare_geometry,geometry
from scene_fidelity import audit_fidelity,entities
from runtime_fidelity import audit_health


class Session:
    def __init__(self, exe, folder, fixture, replica, callback, identities=None, level=None, live_world=False,direct_control=False):
        folder.mkdir(parents=True)
        self.folder = folder
        self.messages = []
        env = dict(os.environ)
        env.update(USERPROFILE=str(folder), APPDATA=str(folder), LOCALAPPDATA=str(folder),
                   REPOPULATED_DIAGNOSTIC_LOG=str(folder / 'native-telemetry.jsonl'))
        env.pop('REPOPULATED_TEST_CONTROL', None)
        root = Path(__file__).resolve().parents[1]
        config = {'sandbox':str(folder), 'dll':str(root / '.runtime' / 'RepopulatedDiagnostic.dll'),
                  'replica':replica, 'networkControl':not replica, 'streamState':not replica}
        if direct_control and not replica:
            config['directControl']=True
        if not replica:
            config['exportFactions']=[7,8]
        elif identities:
            config['initialIdentities']=identities
        if live_world:
            config.update(worldReplica=replica,worldStream=not replica,fireTest=not replica,damageTest=not replica)
        commands = (f'level_load {level.as_posix()}; sleep 30; echo finished' if level else
                    f'import {fixture.as_posix()}; activate; sleep 30; echo finished')
        self.device = frida.get_local_device()
        self.pid = self.device.spawn([str(exe), 'kNetworkEnable=0', 'kHeadlessMode=1',
                                     'kSandboxScript=' + json.dumps(commands)], env=env, stdio='pipe')
        try:
            self.session = self.device.attach(self.pid)
            self.script = self.session.create_script(SCRIPT.replace('__CONFIG__', json.dumps(config)))

            def message(msg, data):
                record = msg.get('payload', msg)
                self.messages.append(record)
                callback(record)

            self.script.on('message', message)
            self.script.load()
        except Exception:
            self.device.kill(self.pid)
            raise

    def resume(self):
        self.device.resume(self.pid)

    def close(self):
        try:
            self.device.kill(self.pid)
        except frida.ProcessNotFoundError:
            pass
        (self.folder / 'instrumentation.json').write_text(json.dumps(self.messages, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--live-clusters',action='store_true',help='Reconstruct changing serialized clusters including fired missiles')
    args = parser.parse_args()
    exe = args.exe.resolve()
    if hashlib.sha256(exe.read_bytes()).hexdigest() != KNOWN_HASH:
        raise ValueError('Unknown executable build')
    root = Path(__file__).resolve().parents[1]
    folder = root / '.runtime' / ('replication-check-' + str(time.time_ns()))
    fixture = folder / 'fixture'
    fixture.mkdir(parents=True)
    ships = exe.parent.parent / 'data' / 'ships'
    for name in ['8_interceptor.lua','7_1.lua']:
        (fixture / name).write_bytes((ships / name).read_bytes())
    outbound = queue.Queue(maxsize=64)
    failures = []
    stopped = threading.Event()
    bootstrap_ready = threading.Event()
    bootstrap_files = []
    # socketpair is a real local socket transport; Windows implements it with
    # loopback TCP. This isolated test exposes no listening game server.
    sender, receiver = socket.socketpair()
    sender.settimeout(3)
    receiver.settimeout(3)
    host = replica = bridge = None
    previous = Path.cwd()
    try:
        os.chdir(exe.parent)
        def receive():
            try:
                with receiver.makefile('rb') as stream:
                    while not stopped.is_set():
                        raw = stream.readline(3*1024*1024+1 if args.live_clusters else 8193)
                        if not raw:
                            return
                        limit=3*1024*1024 if args.live_clusters else 8192
                        if len(raw)>limit or not raw.endswith(b'\n'):
                            raise ValueError('Snapshot framing failed')
                        state = json.loads(raw)
                        if args.live_clusters:
                            data=base64.b64decode(state['payload'],validate=True)
                            if len(data)>2*1024*1024 or hashlib.sha256(data).hexdigest()!=state['sha256']:
                                raise ValueError('Live cluster payload hash mismatch')
                            if len(geometry(data))!=state['roots']:
                                raise ValueError('Invalid live cluster identity or structure')
                            path=folder / 'client' / f"network-world-{state['seq']}.lua"
                            path.write_bytes(data)
                            replica.script.exports_sync.applyworld({'seq':state['seq'],'path':str(path),'roots':state['roots'],
                                                                   'sha256':state['sha256']})
                            continue
                        if {s['faction'] for s in state['ships']} != {7,8}:
                            raise ValueError('Unexpected fixture identity')
                        replica.script.exports_sync.applystate(state)
            except Exception as error:
                if not stopped.is_set():
                    failures.append(str(error))

        def transmit():
            try:
                while not stopped.is_set():
                    try:
                        state = outbound.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    sender.sendall(json.dumps(state, allow_nan=False).encode()+b'\n')
            except Exception as error:
                if not stopped.is_set():
                    failures.append(str(error))

        def state_from_host(record):
            if record.get('type') == 'bootstrap-exported':
                bootstrap_files.extend(record['files'])
                bootstrap_ready.set()
            state=None
            if args.live_clusters and record.get('type')=='world-exported':
                if record['roots']<0:
                    failures.append('Native world export failed'); return
                data=Path(record['path']).read_bytes()
                state={'seq':record['seq'],'roots':record['roots'],'sha256':hashlib.sha256(data).hexdigest(),
                       'payload':base64.b64encode(data).decode()}
            elif not args.live_clusters and record.get('type') == 'authoritative-state':
                state=record
            if state:
                try:
                    outbound.put_nowait(state)
                except queue.Full:
                    failures.append('Snapshot queue overflow')

        host = Session(exe, folder / 'host', fixture, False, state_from_host,live_world=args.live_clusters)
        bridge = ControlBridge(lambda command: host.script.exports_sync.enqueue(command))
        host.resume()
        controls = bridge.exercise(include_fire=args.live_clusters)
        if not bootstrap_ready.wait(3) or len(bootstrap_files)!=2 or any(f['bytes']<=0 for f in bootstrap_files):
            raise RuntimeError('Native host ship bootstrap failed')
        bootstrap = []
        for file in bootstrap_files:
            data = Path(file['path']).read_bytes()
            identity=re.search(rb'command=\{[^}]*?ident=(0x[0-9a-fA-F]+|\d+)',data)
            if not identity:
                raise RuntimeError('Serialized ship lacks persistent command ID')
            bootstrap.append({'faction':file['faction'],'sha256':hashlib.sha256(data).hexdigest(),
                              'ident':int(identity[1],0),
                              'payload':base64.b64encode(data).decode()})
        wire = json.dumps(bootstrap).encode()+b'\n'
        if len(wire)>65536:
            raise RuntimeError('Fixture bootstrap exceeds bounded message size')
        sender.sendall(wire)
        incoming=bytearray()
        while not incoming.endswith(b'\n'):
            chunk=receiver.recv(4096)
            if not chunk:
                raise RuntimeError('Bootstrap stream closed')
            incoming.extend(chunk)
            if len(incoming)>65536:
                raise RuntimeError('Bootstrap framing limit')
        client_fixture=folder / 'client-fixture'
        client_fixture.mkdir()
        cluster_payloads=[]
        for ship in json.loads(incoming):
            if ship['faction'] not in (7,8):
                raise RuntimeError('Unexpected bootstrap faction')
            data=base64.b64decode(ship['payload'],validate=True)
            if hashlib.sha256(data).hexdigest()!=ship['sha256']:
                raise RuntimeError('Bootstrap payload hash mismatch')
            (client_fixture / f"faction-{ship['faction']}.lua").write_bytes(data)
            cluster_payloads.append(b'cluster'+data.strip()+b'\n')
        level=client_fixture / 'bootstrap-level.lua'
        level.write_bytes(b'offset={0,0}\nradius={9000,9000}\nviewpos={3000,3000,1000}\n'+b''.join(cluster_payloads))
        replica = Session(exe, folder / 'client', client_fixture, True, lambda record: None,
                          [{'faction':s['faction'],'ident':s['ident']} for s in bootstrap], level,args.live_clusters)
        threads = [threading.Thread(target=receive,daemon=True),threading.Thread(target=transmit,daemon=True)]
        for thread in threads:
            thread.start()
        replica.resume()
        deadline = time.monotonic()+7
        while time.monotonic()<deadline:
            time.sleep(0.2)
        native_errors = [m for session in [host,replica] for m in session.messages if m.get('type')=='error']
        if args.live_clusters:
            applied=[m for m in replica.messages if m.get('type')=='world-applied']
            exports=[m for m in host.messages if m.get('type')=='world-exported']
            if failures or native_errors or len(applied)<3 or any(m['clusters']!=m['expectedRoots'] for m in applied):
                raise RuntimeError('Live cluster reconstruction failed: '+str(failures+native_errors)+str(applied))
            counts=[m['clusters'] for m in applied]
            exact=[hashlib.sha256(Path(m['verifiedPath']).read_bytes()).hexdigest()==m['inputSha256'] for m in applied]
            fidelity=[]
            health_audits=[]
            host_exports={m['seq']:m for m in exports}
            for update in applied:
                source=(folder / 'client' / f"network-world-{update['seq']}.lua").read_bytes()
                restored=Path(update['verifiedPath']).read_bytes()
                if compare_geometry(source,restored)!=update['clusters']:
                    raise RuntimeError('Reconstructed cluster geometry count differs')
                audit=audit_fidelity(source,restored)
                audit['seq']=update['seq']
                fidelity.append(audit)
                native_health=audit_health(host_exports[update['seq']]['runtimeHealth'],update['runtimeHealth'])
                native_health['seq']=update['seq']; health_audits.append(native_health)
            damage=[m for m in host.messages if m.get('type')=='fixture-damage-result']
            if len(damage)!=1 or damage[0]['result']!=1:
                raise RuntimeError('Native damage injection failed')
            partial=[m for m in host.messages if m.get('type')=='fixture-partial-damage-result']
            if len(partial)!=1 or partial[0]['result']!=1:
                raise RuntimeError('Native nonlethal damage injection failed')
            network_fire=[m for m in host.messages if m.get('type')=='network-fire-result']
            if len(network_fire)!=2 or any(m['result']<0 for m in network_fire) or not any(m['result']>0 for m in network_fire):
                raise RuntimeError('Authenticated native weapon input failed')
            player_ident=next(s['ident'] for s in bootstrap if s['faction']==8)
            partial_health=[]
            for update in applied:
                source=entities((folder / 'client' / f"network-world-{update['seq']}.lua").read_bytes())[player_ident]
                restored=entities(Path(update['verifiedPath']).read_bytes())[player_ident]
                def damaged_thrusters(cluster):
                    return sorted(block['fields']['health'] for block in cluster['fields']['blocks']['items']
                                  if block['items'][0]==803 and 'health' in block['fields'])
                expected,actual=damaged_thrusters(source),damaged_thrusters(restored)
                if expected!=actual or any(value<=0 for value in expected):
                    raise RuntimeError('Nonlethal thruster health differs in replica')
                partial_health.extend(expected)
            if not partial_health:
                raise RuntimeError('No nonlethal thruster damage reached the replica')
            shapes=[geometry(Path(update['verifiedPath']).read_bytes())[player_ident]['blocks'] for update in applied]
            thrusters=[sum(block[0]==803 for block in shape) for shape in shapes]
            if thrusters[-1]!=thrusters[0]-1:
                raise RuntimeError('Destroyed thruster was not removed from replica')
            if max(counts)<=2 or len(set(counts))<2:
                raise RuntimeError('No changing weapon cluster scene reconstructed')
            result={'schema':1,'liveNativeClusterSceneValidated':True,'fullWorldReplicationValidated':False,
                    'campaignValidated':False,'multiplayerValidated':False,'exports':len(exports),
                    'clientUpdates':len(applied),'clientClusterCounts':counts,'lastApplied':applied[-1]}
            result['byteIdenticalLiveSnapshotsValidated']=all(exact)
            result['liveGeometryAndIdentitiesValidated']=True
            result['hydrationDifferencesDetected']=not all(exact)
            result['serializedStateEquivalent']=all(a['serializedStateEquivalent'] for a in fidelity)
            result['stateFidelityAudits']=fidelity
            result['nativeHealthAudits']=health_audits
            result['nativeHealthMultisetsEquivalent']=all(a['nativeHealthMultisetsEquivalent'] for a in health_audits)
            result['nativePartialDamageInjected']=True
            result['authenticatedNativeWeaponInputValidated']=True
            result['weaponControls']=controls
            result['nativePartialDamageReplicationValidated']=True
            result['replicatedDamagedThrusterHealth']=partial_health
            result['serializedHealthDifferences']=[d for a in fidelity for d in a['differences']
                                                  if d['path'].endswith('.health')]
            result['nativeBlockDestructionReplicationValidated']=True
            result['clientThrusterCounts']=thrusters
            result['entityRemovalObserved']=any(after<before for before,after in zip(counts,counts[1:]))
            (folder / 'result.json').write_text(json.dumps(result,indent=2))
            print('Live weapon cluster scene reconstructed over TCP. Evidence:',folder / 'result.json')
            return
        applied = [m for m in replica.messages if m.get('type')=='replica-state-applied']
        host_states = [m for m in host.messages if m.get('type')=='authoritative-state']
        expected_ids={s['ident'] for s in bootstrap}
        if len(expected_ids)!=2 or any({s['ident'] for s in m['ships']}!=expected_ids for m in host_states):
            raise RuntimeError('Native command identities changed or differ from bootstrap')
        commands = [m for m in host.messages if m.get('type')=='network-waypoint-result']
        if failures or native_errors or len(applied)<3 or any(m['results'] != [1,1] for m in applied):
            raise RuntimeError('Native replication failed: '+str(failures+native_errors)+f'; applied={len(applied)}')
        if len(commands)!=2 or any(m['result']!=1 for m in commands):
            raise RuntimeError('Native host commands failed')
        for faction in [7,8]:
            positions = [next(s for s in m['ships'] if s['faction']==faction)['x'] for m in applied]
            if max(positions)-min(positions)<100:
                raise RuntimeError('Replica did not receive meaningful ship movement')
        # Native setter readbacks confirm each pose immediately after application.
        result = {'schema':1,'nativePoseReplicationValidated':True,
                  'nativeShipBootstrapValidated':True,
                  'persistentShipIdentityValidated':True,
                  'bootstrap':[{'faction':s['faction'],'ident':s['ident'],'sha256':s['sha256']} for s in bootstrap],
                  'fullWorldReplicationValidated':False,'campaignValidated':False,'multiplayerValidated':False,
                  'hostStates':len(host_states),'clientStatesApplied':len(applied),'controls':controls,
                  'lastClientState':applied[-1], 'gameHash':KNOWN_HASH}
        (folder / 'result.json').write_text(json.dumps(result,indent=2))
        print('Two native processes replicated ship poses over TCP. Evidence:',folder / 'result.json')
    finally:
        stopped.set()
        if bridge:
            bridge.close()
        for sock in [sender,receiver]:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in locals().get('threads',[]):
            thread.join(timeout=3)
        for session in [host,replica]:
            if session:
                session.close()
        os.chdir(previous)


if __name__ == '__main__':
    main()
