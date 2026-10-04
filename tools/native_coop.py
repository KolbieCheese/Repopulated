"""Experimental two-player native campaign/visual-client prototype.

Stock faction-8 ships only. Separate from the production lobby and adapter
contract. No remote building, progression UI or distant-sector streaming yet.
The desktop launcher supplies native alpha checkpoints. Native Player handling
produces joining-player navigation and per-block weapon intent.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import queue
import secrets
import socket
import socketserver
import threading
import time
import uuid
from coop_wire import GAME_HASH,PILOT,WIRE_LIMIT,NATIVE_VERSION,snapshot,decode_snapshot,control
from native_session import NativeSession
from presentation_wire import pack_visuals,PRESENTATION_VERSION
from replica_plan import ReplicaPlan,HEADER
from runtime_fidelity import audit_health
from campaign_map_wire import pack_map,map_summary,visible_map,load_map_settings


def content_hash(exe):
    digest=hashlib.sha256()
    data=exe.resolve().parent.parent/'data'
    for path in sorted(data.rglob('*.lua'),key=lambda p:p.relative_to(data).as_posix()):
        digest.update(path.relative_to(data).as_posix().encode()+b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def read_message(stream,limit):
    raw=stream.readline(limit+1)
    if not raw:raise EOFError('Peer disconnected')
    if len(raw)>limit or not raw.endswith(b'\n'):raise ValueError('Frame limit')
    return json.loads(raw)


def send_message(stream,message):
    stream.write(json.dumps(message,allow_nan=False,separators=(',',':')).encode()+b'\n');stream.flush()


def rejection_check(host,address):
    """Bad peers must neither stop the host nor acquire another faction."""
    hello={'type':'hello','token':host.token,'gameHash':GAME_HASH,'contentHash':host.content,'mods':[],'presentationVersion':PRESENTATION_VERSION,'nativeVersion':NATIVE_VERSION}
    with socket.create_connection(address,timeout=3) as peer:
        with peer.makefile('rwb') as stream:
            send_message(stream,dict(hello,token=host.token+'x'))
            if read_message(stream,4096).get('type')!='error':raise RuntimeError('Invalid token was accepted')
    with socket.create_connection(address,timeout=3) as peer:
        with peer.makefile('rwb') as stream:
            send_message(stream,hello)
            if read_message(stream,4096).get('type')!='welcome':raise RuntimeError('Valid token was refused')
            send_message(stream,{'type':'input','seq':0,'action':'drive','x':1,'y':0,'ownerFaction':100})
            # Wait for the invalid envelope to disconnect this peer. Scene or
            # ping frames may already have been published before rejection.
            try:
                while True:read_message(stream,WIRE_LIMIT)
            except EOFError:pass
    deadline=time.monotonic()+3
    while host.peer is not None and time.monotonic()<deadline:time.sleep(0.02)
    if host.peer is not None or host.previous_input!=-1 or len(host.rejections)<2 or host.failures:
        raise RuntimeError('Peer rejection affected the authoritative host')


class CoopHost:
    def __init__(self,exe,folder,address=('127.0.0.1',32916),token=None,test_respawn=False,hold_controls_ms=0,campaign_source=None,test_beams=False,verify_health=False,test_menus=False,shared_exploration=None):
        if shared_exploration is not None and type(shared_exploration) is not bool:raise ValueError('Invalid shared exploration setting')
        saved_settings=load_map_settings(campaign_source) if campaign_source is not None else {'sharedExploration':False}
        self.shared_exploration=saved_settings['sharedExploration'] if shared_exploration is None else shared_exploration
        self.latest_host_map=None
        self.token=token or secrets.token_hex(32);self.content=content_hash(exe)
        self.lock=threading.Lock();self.latest=None;self.peer=None;self.stopped=threading.Event()
        self.failures=[];self.rejections=[];self.previous_input=-1;self.scene_count=0;self.input_count=0
        self.respawns=0;self.first_pose=None;self.maximum_movement=0;self.native_drives=0;self.native_fires=0
        self.active_beam_scenes=0
        self.health_by_sequence={}
        self.save_lock=threading.Lock();self.checkpoint_waiters={}
        host=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                registered=False;sender=None;done=threading.Event()
                try:
                    self.request.settimeout(5)
                    hello=read_message(self.rfile,4096)
                    if not isinstance(hello,dict) or set(hello)!={'type','token','gameHash','contentHash','mods','presentationVersion','nativeVersion'} or hello['type']!='hello':
                        raise ValueError('Invalid hello')
                    if not isinstance(hello['token'],str) or not secrets.compare_digest(hello['token'],host.token):raise ValueError('Authentication failed')
                    if hello['gameHash']!=GAME_HASH or hello['contentHash']!=host.content or hello['mods']!=[]:raise ValueError('Content mismatch')
                    if type(hello['presentationVersion']) is not int or hello['presentationVersion']!=PRESENTATION_VERSION:raise ValueError('Update both multiplayer launchers: presentation protocol differs')
                    if type(hello['nativeVersion']) is not int or hello['nativeVersion']!=NATIVE_VERSION:raise ValueError('Update both multiplayer launchers: native protocol differs')
                    with host.lock:
                        if host.peer is not None:raise ValueError('Remote seat occupied')
                        host.peer=self.request;registered=True
                    host.game.script.exports_sync.setremotecontrol(True)
                    send_message(self.wfile,{'type':'welcome','faction':20008,'ident':PILOT,'nextSeq':host.previous_input+1,'presentationVersion':PRESENTATION_VERSION,'nativeVersion':NATIVE_VERSION,'sharedExploration':host.shared_exploration})
                    def publish():
                        previous=-1;last_ping=0
                        try:
                            while not done.is_set() and not host.stopped.is_set():
                                with host.lock:scene=host.latest
                                if scene and scene['seq']>previous:
                                    send_message(self.wfile,scene);previous=scene['seq']
                                elif time.monotonic()-last_ping>1:
                                    send_message(self.wfile,{'type':'ping'});last_ping=time.monotonic()
                                done.wait(0.02)
                        except (OSError,ValueError):done.set()
                    sender=threading.Thread(target=publish,daemon=True);sender.start()
                    rate_start=time.monotonic();frames=0
                    while not done.is_set() and not host.stopped.is_set():
                        message=read_message(self.rfile,65536)
                        now=time.monotonic()
                        if now-rate_start>=1:rate_start=now;frames=0
                        frames+=1
                        if frames>40:raise ValueError('Input rate limit')
                        if message=={'type':'pong'}:continue
                        command=control(message,host.previous_input)
                        host.previous_input=command['seq'];host.input_count+=1
                        host.game.script.exports_sync.enqueue(command)
                except (EOFError,OSError):pass
                except (ValueError,TypeError) as error:
                    if len(host.rejections)<32:host.rejections.append(str(error))
                    if sender is None:
                        try:send_message(self.wfile,{'type':'error','message':str(error)})
                        except OSError:pass
                except Exception as error:host.failures.append(str(error))
                finally:
                    done.set()
                    if sender:sender.join(timeout=1)
                    if registered:
                        try:host.game.script.exports_sync.setremotecontrol(False)
                        except Exception as error:
                            if not host.stopped.is_set():host.failures.append(str(error))
                        with host.lock:host.peer=None
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
        self.server=Server(address,Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        config={'networkControl':True,'directControl':True,'ownedFactions':[20008],'vacantRemoteAI':True,'campaignCheckpoints':True,
                'activeShips':{'20008':PILOT},'campaignRemote':True,'remoteOffset':[500,0],'remoteRespawn':True,'streamState':True,'identitiesOnly':True,
                'worldStream':True,'interestIdent':PILOT,'interestRadius':2000,'skipHealth':True,'streamPresentation':True,
                'sampleIntervalMs':250,'maxSamples':1000000,'rollingWorld':True,'testRemoteDeath':test_respawn,'syncCampaignMap':True,
                'holdControlsMs':hold_controls_ms,'beamFixture':test_beams,'verifyPilotHealth':verify_health,
                'keepHostRunningInMenus':True,'testHostMenus':test_menus,'sharedExploration':self.shared_exploration}
        if campaign_source is not None:
            config['verifyCampaignRestore']=True
            map_file=campaign_source/'campaign-map.json'
            if map_file.exists():
                if map_file.stat().st_size>2*1024*1024:raise ValueError('Saved campaign map size limit')
                config['restoreCampaignMap']=pack_map(json.loads(map_file.read_text()))
            remote_file=campaign_source/'remote-map.json'
            if remote_file.exists():
                if remote_file.stat().st_size>2*1024*1024:raise ValueError('Saved remote map size limit')
                config['restoreRemoteMap']=pack_map(json.loads(remote_file.read_text()))
            elif map_file.exists():
                # Older shared saves cannot separate already learned knowledge.
                # Retain it for both seats, then apply the newly selected policy.
                config['restoreRemoteMap']=config['restoreCampaignMap']
        def native_message(record):
            if record.get('type')=='checkpoint-complete':
                with self.lock:waiter=self.checkpoint_waiters.get(record['id'])
                if waiter:
                    waiter[1].append(record);waiter[0].set()
            if record.get('type')=='error':self.failures.append(record.get('description','Native instrumentation error'))
            if record.get('type')=='remote-respawn' and record.get('result')==1:self.respawns+=1
            if record.get('type')=='network-drive-result' and record.get('result')==1:self.native_drives+=1
            if record.get('type')=='network-fire-result' and record.get('result',0)>0:self.native_fires+=1
            if record.get('type')!='world-exported':return
            try:
                if record['roots']==-10:return # Waiting for the remote respawn cooldown.
                if record['roots']<0:raise ValueError('Interest export failed: '+str(record['roots']))
                data=Path(record['path']).read_bytes()
                visuals=json.loads(Path(record['visualPath']).read_text())
                campaign_map=json.loads(Path(record['mapPath']).read_text())
                remote_map=json.loads(Path(record['remoteMapPath']).read_text())
                expected='shared' if self.shared_exploration else 'independent'
                if campaign_map['exploration']!=expected or remote_map['exploration']!=expected:raise ValueError('Native exploration policy differs')
                self.latest_host_map=campaign_map
                campaign_map=visible_map(remote_map)
                scene,pose=snapshot(data,record['seq'],record['roots'],with_pose=True,visuals=visuals,campaign_map=campaign_map)
                if any(row[8]>0 for row in visuals['blocks']):self.active_beam_scenes+=1
                if self.first_pose is None:self.first_pose=pose
                self.maximum_movement=max(self.maximum_movement,math.hypot(pose[0]-self.first_pose[0],pose[1]-self.first_pose[1]))
                with self.lock:
                    self.health_by_sequence[record['seq']]=record.get('pilotHealth',[])
                    while len(self.health_by_sequence)>16:self.health_by_sequence.pop(next(iter(self.health_by_sequence)))
                    self.latest=scene
                self.scene_count+=1
            except Exception as error:
                if len(self.failures)<32:
                    if 'data' in locals():(self.game.folder/('rejected-scene-'+str(record['seq'])+'.lua')).write_bytes(data)
                    self.failures.append(str(error))
        try:self.game=NativeSession(exe,folder,config,native_message,campaign=True,campaign_source=campaign_source)
        except Exception:self.server.server_close();raise
        self.thread.start();self.game.resume()

    def checkpoint(self,store,name='Multiplayer alpha'):
        with self.save_lock:
            ident=uuid.uuid4().hex;path=self.game.folder/('checkpoint-'+ident);waiter=(threading.Event(),[])
            with self.lock:self.checkpoint_waiters[ident]=waiter
            try:
                self.game.script.exports_sync.checkpoint({'id':ident,'path':str(path)})
                if not waiter[0].wait(20):raise RuntimeError('Native checkpoint timed out')
                record=waiter[1][0]
                if record['files']<3 or record['roots']!=2:raise RuntimeError('Native checkpoint failed: '+str(record))
                (path/'player-health.json').write_text(json.dumps(record['playerHealth']))
                (path/'map-settings.json').write_text(json.dumps({'version':1,'sharedExploration':self.shared_exploration}))
                return store.publish(path,self.content,name)
            finally:
                with self.lock:self.checkpoint_waiters.pop(ident,None)

    def close(self):
        with self.save_lock:self._close()

    def _close(self):
        self.stopped.set()
        with self.lock:
            if self.peer:
                try:self.peer.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.game.close()


def run_client(exe,folder,address,token,seconds=3600,test_controls=False,stop_event=None,native_campaign=True,
               control_scheme='MOUSE_ROT',notice_callback=None,health_validator=None,progress_callback=None,test_menus=False):
    if control_scheme not in ('MOUSE_ROT','KEY_ROT','CARDINAL'):raise ValueError('Unknown native control scheme')
    game=None;reader=None;stopped=threading.Event();outbound=queue.Queue(maxsize=4)
    failures=[];updates=[];sequence=0;pilot_pose=[3000,3000,0,0,0];controls_started=None;received_beam_scenes=0
    sock=socket.create_connection(address,timeout=10);sock.settimeout(10)
    stream=sock.makefile('rwb');started=time.monotonic()
    try:
        send_message(stream,{'type':'hello','token':token,'gameHash':GAME_HASH,'contentHash':content_hash(exe),'mods':[],'presentationVersion':PRESENTATION_VERSION,'nativeVersion':NATIVE_VERSION})
        welcome=read_message(stream,4096)
        if welcome.get('type')!='welcome' or welcome.get('ident')!=PILOT or welcome.get('faction')!=20008 or welcome.get('presentationVersion')!=PRESENTATION_VERSION or welcome.get('nativeVersion')!=NATIVE_VERSION or type(welcome.get('sharedExploration')) is not bool:
            raise ValueError('Server refused native client: '+str(welcome))
        sequence=welcome['nextSeq']
        while True:
            first=read_message(stream,WIRE_LIMIT)
            if first.get('type')=='ping':send_message(stream,{'type':'pong'});continue
            initial,pilot_pose=decode_snapshot(first,0);break
        latest_seq=first['seq'];pending=None;planner=ReplicaPlan(HEADER)
        def native_message(record):
            nonlocal sequence,pending,controls_started
            kind=record.get('type')
            if kind=='error':failures.append(record.get('description','Native error'))
            if kind=='native-client-notice' and notice_callback:notice_callback(record['message'])
            if kind=='world-applied':
                updates.append(record)
                if progress_callback:progress_callback(record)
                if health_validator:
                    try:health_validator(record)
                    except Exception as error:failures.append('Native pilot health check failed: '+str(error))
                if record['verifiedRoots']!=record['expectedRoots']:failures.append('Native replica root count differs')
                consumed=pending
                if consumed:
                    if record['clusters']<0:failures.append('Incremental native apply failed: '+str(record['clusters']))
                    else:planner.commit(consumed['plan'])
                    if native_campaign:
                        verify=Path(record['campaignMap']['verifiedPath'])
                        try:
                            actual=json.loads(verify.read_text());expected=consumed['map']
                            if actual['exploration']!=expected['exploration'] or actual['radius']!=expected['radius'] or actual['width']!=expected['width'] or actual['regions']!=expected['regions'] or [r[:3] for r in actual['cells']]!=[r[:3] for r in expected['cells']] or actual['objectives']!=expected['objectives']:
                                (game.folder/('map-mismatch-'+str(record['seq'])+'.json')).write_text(json.dumps({'actual':actual,'expected':expected}))
                                raise ValueError('Authoritative native map differs after apply')
                        except Exception as error:failures.append('Native map check failed: '+str(error))
                        finally:verify.unlink(missing_ok=True)
                        record['campaignMap'].pop('verifiedPath',None)
                    try:consumed['path'].unlink(missing_ok=True)
                    except OSError:pass
                pending=None
                try:Path(record['verifiedPath']).unlink(missing_ok=True)
                except OSError:pass
            if native_campaign and kind=='native-navigation-intent':
                if record['ident']!=PILOT:return
                if controls_started is None:controls_started=time.monotonic()
                message={'type':'input','seq':sequence,'action':'native','dimensions':record['dimensions'],
                         'destination':record['destination'],'precision':record['precision'],'weapons':record['weapons']}
                sequence+=1
                if test_controls:
                    held=time.monotonic()-controls_started<4
                    message.update(dimensions=0x106,destination=[0,0,200 if held else 0,0,0,0])
                    message['weapons']=[[row[0],features&0x800008e0 if held else 0,1000,0,0,0,0] for row,features in zip(record['weapons'],record['weaponFeatures'])]
                control(message,message['seq']-1)
                messages=[message]
            elif kind=='native-pilot-input':
                if controls_started is None:controls_started=time.monotonic()
                x,y=record['x'],record['y']
                if test_controls:x=1 if time.monotonic()-controls_started<4 else 0;y=0
                aim=record.get('aim')
                if test_controls:aim=[1000,0]
                messages=[{'type':'input','seq':sequence,'action':'drive','x':x,'y':y}];sequence+=1
                if aim and math.hypot(*aim)>1:messages[0]['angle']=math.atan2(aim[1],aim[0])
                angle=pilot_pose[4]
                offset=aim if aim else [math.cos(angle)*1000,math.sin(angle)*1000]
                messages.append({'type':'input','seq':sequence,'action':'fire',
                                 'held':bool(record['fire'] or test_controls and time.monotonic()-controls_started<4),
                                 'x':pilot_pose[0]+offset[0],'y':pilot_pose[1]+offset[1]});sequence+=1
            else:return
            try:outbound.put_nowait(messages)
            except queue.Full:
                # Flight/aim are continuous state, not an event backlog.
                try:outbound.get_nowait()
                except queue.Empty:pass
                try:outbound.put_nowait(messages)
                except queue.Full:pass
        config={'replica':True,'worldReplica':True,'persistentReplica':True,'renderOnly':True,'pilotInput':True,'followPilot':PILOT,'skipHealth':True,'predictPresentation':True,
                'measureRendering':test_controls,'replicatePresentation':True,
                'frameLimit':60,'sampleIntervalMs':100,'maxSamples':1000000,'verifyPilotHealth':health_validator is not None}
        if native_campaign:config.update(nativeCampaignClient=True,captureNativeIntent=True,controlScheme=control_scheme,syncCampaignMap=True,testNativeMenus=test_menus,sharedExploration=welcome['sharedExploration'])
        game=NativeSession(exe,folder,config,native_message,campaign=native_campaign,bootstrap=initial)
        def receive():
            nonlocal latest_seq,pilot_pose,pending,received_beam_scenes
            try:
                while not stopped.is_set():
                    scene=read_message(stream,WIRE_LIMIT)
                    if scene.get('type')=='ping':
                        try:outbound.put_nowait([{'type':'pong'}])
                        except queue.Full:pass
                        continue
                    if pending is not None:
                        # Discard an obsolete snapshot before parsing its Lua.
                        # Only a fully validated scene can reach the native loader.
                        if scene.get('type')!='scene' or type(scene.get('seq')) is not int or not latest_seq<scene['seq']<=10000000:
                            raise ValueError('Invalid pending scene sequence')
                        latest_seq=scene['seq'];continue
                    data,pose=decode_snapshot(scene,latest_seq);latest_seq=scene['seq'];pilot_pose=pose
                    if any(row[8]>0 for row in scene['visuals']['blocks']):received_beam_scenes+=1
                    plan=planner.prepare(data)
                    path=game.folder/f"network-world-{scene['seq']}.lua"
                    if plan['additions'] is not None:path.write_bytes(plan['additions'])
                    pending={'path':path,'plan':plan,'map':scene['map']}
                    game.script.exports_sync.applyworld({'seq':scene['seq'],'path':str(path),'roots':scene['roots'],'sha256':scene['sha256'],
                                                         'visuals':pack_visuals(scene['visuals']),
                                                         'incremental':{key:plan[key] for key in ('remove','poses','health','retained','replaced')},
                                                         'hasAdditions':plan['additions'] is not None,
                                                         'map':pack_map(scene['map']) if native_campaign else None,
                                                         'mapSummary':map_summary(scene['map']) if native_campaign else None})
            except Exception as error:
                if not stopped.is_set():failures.append(str(error))
        reader=threading.Thread(target=receive,daemon=True);reader.start();game.resume()
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline and not failures and not (stop_event and stop_event.is_set()):
            try:messages=outbound.get(timeout=0.1)
            except queue.Empty:continue
            for message in messages:send_message(stream,message)
        return {'updates':len(updates),'displayFrames':max((m.get('displayFrames',0) for m in updates),default=0),
                'pilotRenderCalls':max((m.get('pilotRenderCalls',0) for m in updates),default=0),
                'eventPollCalls':max((m.get('pollCalls',0) for m in updates),default=0),
                'nativeInputMessages':sum(m.get('type')==('native-navigation-intent' if native_campaign else 'native-pilot-input') for m in game.messages),
                'nativeCampaignClient':native_campaign,
                'controlScheme':control_scheme,
                'failures':failures,'lastPilotPose':pilot_pose,'testControls':test_controls,
                'frameTiming':frame_timing(updates[-1].get('frameTiming',{})) if updates else {},
                'predictedFrames':max((m.get('predictedFrames',0) for m in updates),default=0),
                'presentation':updates[-1].get('presentation',{}) if updates else {},'receivedBeamScenes':received_beam_scenes,
                'persistentReplica':updates[-1].get('persistentReplica',{}) if updates else {},
                'pilotLifetimes':pilot_lifetimes(updates),'map':updates[-1].get('campaignMap',{}) if updates else {},
                'nativeMenuTests':[m for m in game.messages if m.get('type')=='native-menu-test']}
    finally:
        stopped.set()
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        if reader:reader.join(timeout=2)
        stream.close();sock.close()
        if game:game.close()


def pilot_lifetimes(updates):
    runs=[]
    for message in updates:
        pointer=message.get('pilotPointer')
        if not pointer:continue
        if runs and runs[-1]['pointer']==pointer:runs[-1]['updates']+=1
        else:runs.append({'pointer':pointer,'firstSequence':message['seq'],'updates':1})
    return {'lifetimes':len(runs),'longestRetainedUpdates':max((r['updates'] for r in runs),default=0),'changes':runs}


def frame_timing(timing):
    histogram=timing.get('histogram',[]);count=sum(histogram)
    threshold=count*0.95;seen=0;p95=None
    for ms,number in enumerate(histogram):
        seen+=number
        if seen>=threshold and count:p95=ms;break
    return {'sampledFrames':count,'p95Ms':p95,'framesOver50Ms':timing.get('longFrames',0),'maxMs':timing.get('maxFrameMs',0)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['host','join','check'])
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--address',default='127.0.0.1');parser.add_argument('--port',type=int,default=32916)
    parser.add_argument('--token');parser.add_argument('--seconds',type=int,default=3600)
    parser.add_argument('--check-respawn',action='store_true',help='In check mode, destroy the private remote command block and verify respawn')
    parser.add_argument('--check-backpressure',action='store_true',help='Hold native control consumption for four seconds to exercise coalescing')
    parser.add_argument('--check-beams',action='store_true',help='Test-only stock beam mount in both private ships')
    parser.add_argument('--check-health',action='store_true',help='Damage a private thruster and compare host/client runtime health')
    parser.add_argument('--check-map-menus',action='store_true',help='Exercise the native map and binding overlays on the game thread')
    parser.add_argument('--check-host-menus',action='store_true',help='Keep the authoritative world advancing under host map/binding overlays')
    parser.add_argument('--shared-exploration',action='store_true',help='Share discovery between factions; separate discovery is the default')
    parser.add_argument('--native-campaign-client',action=argparse.BooleanOptionalAction,default=True,help='Native Player/HUD client (default); disable for the older sandbox bridge')
    parser.add_argument('--control-scheme',choices=['MOUSE_ROT','KEY_ROT','CARDINAL'],default='MOUSE_ROT')
    args=parser.parse_args()
    if not 1<=args.port<=65535 or not 5<=args.seconds<=3600:parser.error('Invalid port or duration')
    if args.check_respawn and (args.mode!='check' or args.seconds<10):parser.error('Respawn check requires check mode and at least ten seconds')
    if args.check_backpressure and (args.mode!='check' or args.seconds<10):parser.error('Backpressure check requires check mode and at least ten seconds')
    if args.check_health and args.mode!='check':parser.error('Health check requires check mode')
    root=Path(__file__).resolve().parents[1]/'.runtime';stamp=str(time.time_ns())
    host=None
    try:
        if args.mode in ('host','check'):
            host=CoopHost(args.exe,root/('coop-host-'+stamp),(args.address,args.port),args.token,test_respawn=args.check_respawn,
                          hold_controls_ms=4000 if args.check_backpressure else 0,test_beams=args.check_beams,verify_health=args.check_health,test_menus=args.check_host_menus,shared_exploration=args.shared_exploration)
            if args.mode=='host':
                print('Experimental native cooperative host:',args.address,args.port,flush=True)
                print('Remote join token:',host.token,flush=True)
                print('Join with native campaign controls and HUD. Stock ships; private campaign. Use the desktop launcher for saved galaxies.',flush=True)
                deadline=time.monotonic()+args.seconds
                while time.monotonic()<deadline and not host.failures:time.sleep(0.2)
                if host.failures:raise RuntimeError(str(host.failures[:3]))
                return
        if args.mode=='join' and not args.token:parser.error('--token required for join')
        if args.mode=='check':rejection_check(host,(args.address,args.port))
        health_checks={'snapshotsCompared':0,'blocksCompared':0,'maximumHealthError':0}
        def validate_health(record):
            with host.lock:expected=host.health_by_sequence.get(record['seq'])
            if not expected:raise ValueError('Matching authoritative health sample unavailable')
            report=audit_health(expected,record['pilotHealth'])
            if not report['nativeHealthMultisetsEquivalent']:raise ValueError('Runtime damage differs: '+str(report['maximumHealthError']))
            health_checks['snapshotsCompared']+=1;health_checks['blocksCompared']+=report['blocksCompared']
            health_checks['maximumHealthError']=max(health_checks['maximumHealthError'],report['maximumHealthError'])
        result=run_client(args.exe,root/('coop-client-'+stamp),(args.address,args.port),host.token if host else args.token,
                          args.seconds,test_controls=args.mode=='check',native_campaign=args.native_campaign_client,control_scheme=args.control_scheme,
                          health_validator=validate_health if args.check_health else None,test_menus=args.check_map_menus)
        if args.check_health:result['healthValidation']=health_checks
        if host:
            deadline=time.monotonic()+3
            while host.peer is not None and time.monotonic()<deadline:time.sleep(0.02)
            result.update(hostScenes=host.scene_count,hostInputs=host.input_count,hostFailures=host.failures,
                          hostActiveBeamScenes=host.active_beam_scenes,
                          nativeDrives=host.native_drives,nativeFires=host.native_fires,maximumMovement=host.maximum_movement,
                          remoteRespawns=host.respawns,rejectedPeers=len(host.rejections),campaignRemoteControlValidated=False,playableMultiplayerValidated=False)
            result['controlQueue']=host.game.script.exports_sync.controlstats()
            result['hostMenuTests']=[m for m in host.game.messages if m.get('type')=='host-menu-test']
            result['campaignRemoteControlValidated']=not result['failures'] and not host.failures and result['updates']>=3 and \
                result['pilotRenderCalls']>=10 and host.native_drives>=10 and host.native_fires>=1 and host.maximum_movement>=20
        (root/('coop-check-'+stamp+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
        if result['failures'] or host and host.failures:raise RuntimeError('Native cooperative prototype check failed')
        if args.mode=='check' and (result['updates']<3 or result['displayFrames']<10 or result['nativeInputMessages']<10
                                  or result['pilotRenderCalls']<10 or result['eventPollCalls']<10):
            raise RuntimeError('Native rendered client did not establish its loop')
        if args.mode=='check' and not result['campaignRemoteControlValidated']:raise RuntimeError('Campaign controls, motion or firing were not verified')
        if args.check_respawn and not result['remoteRespawns']:raise RuntimeError('Remote respawn was not verified')
        if args.check_backpressure and (result['controlQueue']['peak']>2 or result['controlQueue']['coalesced']<5 or result['controlQueue']['dropped'] or not result['controlQueue']['testHoldCompleted']):
            raise RuntimeError('Continuous control coalescing was not verified')
        if args.mode=='check' and (result['presentation'].get('thrustEmissions',0)<10 or result['presentation'].get('projectileDraws',0)<10 or result['presentation'].get('turretsApplied',0)<10):
            raise RuntimeError('Native presentation replication was not verified')
        if args.check_beams and not result['presentation'].get('beamRenderCalls',0):raise RuntimeError('Active native beam rendering was not verified')
        if args.check_map_menus:
            events=result['nativeMenuTests']
            if [(m['action'],m['tab']) for m in events]!=[('opened',1),('closed',1),('opened',16),('closed',16)] or any(events[i+1]['displayFrames']-events[i]['displayFrames']<60 or events[i+1]['menuSnapshotApplies']-events[i]['menuSnapshotApplies']<3 for i in (0,2)):
                raise RuntimeError('Native map/binding open, render, close cycle was not verified')
        if args.check_host_menus:
            events=result['hostMenuTests']
            if [(m['action'],m['tab']) for m in events]!=[('opened',1),('closed',1),('opened',16),('closed',16)] or any(events[i+1]['hostMenuUpdates']-events[i]['hostMenuUpdates']<60 or events[i+1]['hostMenuBrakes']-events[i]['hostMenuBrakes']<30 or events[i+1]['hostSnapshots']-events[i]['hostSnapshots']<5 for i in (0,2)):
                raise RuntimeError('Host menu simulation continuity was not verified')
        if args.check_health and (health_checks['snapshotsCompared']<3 or not any(m.get('type')=='fixture-partial-damage-result' and m.get('result')==1 for m in host.game.messages)):
            raise RuntimeError('Native partial damage replication was not verified')
        if args.mode=='check' and (result['controlQueue']['playerControlled'] or not result['controlQueue']['vacantAICalls']):
            raise RuntimeError('Vacant faction AI takeover was not verified')
    finally:
        if host:host.close()


if __name__=='__main__':main()
