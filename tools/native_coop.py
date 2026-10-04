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
from coop_wire import GAME_HASH,PILOT,WIRE_LIMIT,NATIVE_VERSION,decode_snapshot,control,coalesce_inputs
from motion_wire import validate_motion
from native_session import NativeSession
from presentation_wire import pack_visuals,PRESENTATION_VERSION
from runtime_fidelity import audit_health
from campaign_map_wire import pack_map,map_summary,visible_map,load_map_settings
from scene_worker import SceneWorker


class DeliveryTiming:
    """Bounded millisecond distributions; a render FPS counter hides stalls."""
    def __init__(self):
        self.count=0;self.total=0;self.maximum=0;self.histogram=[0]*501

    def record(self,milliseconds):
        milliseconds=max(0,milliseconds)
        self.count+=1;self.total+=milliseconds;self.maximum=max(self.maximum,milliseconds)
        self.histogram[min(500,int(milliseconds))]+=1

    def summary(self):
        count=self.count
        if not count:return {'samples':0,'meanMs':0,'p95Ms':0,'maxMs':0}
        total=0;p95=0
        for index,value in enumerate(self.histogram):
            total+=value
            if total>=math.ceil(count*0.95):p95=index;break
        return {'samples':count,'meanMs':self.total/count,'p95Ms':p95,'maxMs':self.maximum}


class DeliverySize:
    def __init__(self):self.count=0;self.total=0;self.maximum=0
    def record(self,size):self.count+=1;self.total+=size;self.maximum=max(self.maximum,size)
    def summary(self):return {'samples':self.count,'meanBytes':self.total/self.count if self.count else 0,'maxBytes':self.maximum}


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


def send_message(stream,message,timing=None,prefix=''):
    started=time.perf_counter()
    payload=json.dumps(message,allow_nan=False,separators=(',',':')).encode()+b'\n'
    encoded=time.perf_counter()
    stream.write(payload);stream.flush()
    if timing is not None:
        timing.setdefault(prefix+'Encode',DeliveryTiming()).record((encoded-started)*1000)
        timing.setdefault(prefix+'Send',DeliveryTiming()).record((time.perf_counter()-encoded)*1000)
        timing.setdefault(prefix+'Bytes',DeliverySize()).record(len(payload))


def read_initial_world(sock,stream,timeout_seconds=20):
    """Heartbeats cannot keep an uninitialized client waiting indefinitely."""
    deadline=time.monotonic()+timeout_seconds;previous_timeout=sock.gettimeout()
    try:
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise RuntimeError('Host did not provide an initial world within '+str(timeout_seconds)+' seconds')
            sock.settimeout(remaining)
            try:first=read_message(stream,WIRE_LIMIT)
            except socket.timeout:raise RuntimeError('Host did not provide an initial world within '+str(timeout_seconds)+' seconds') from None
            if first.get('type')=='error':raise RuntimeError(first.get('message','Host native session failed'))
            if first.get('type')=='ping':send_message(stream,{'type':'pong'});continue
            if first.get('type')=='motion':validate_motion(first);continue
            initial,pose=decode_snapshot(first,0)
            return first,initial,pose
    finally:sock.settimeout(previous_timeout)


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
    def __init__(self,exe,folder,address=('127.0.0.1',32916),token=None,test_respawn=False,hold_controls_ms=0,campaign_source=None,test_beams=False,verify_health=False,test_menus=False,shared_exploration=None,session_factory=None):
        if shared_exploration is not None and type(shared_exploration) is not bool:raise ValueError('Invalid shared exploration setting')
        saved_settings=load_map_settings(campaign_source) if campaign_source is not None else {'sharedExploration':False}
        self.shared_exploration=saved_settings['sharedExploration'] if shared_exploration is None else shared_exploration
        self.latest_host_map=None
        self.token=token or secrets.token_hex(32);self.content=content_hash(exe)
        self.lock=threading.Lock();self.latest=None;self.latest_motion=None;self.motion_count=0;self.peer=None;self.stopped=threading.Event()
        self.failures=[];self.rejections=[];self.peer_disconnects=[];self.previous_input=-1;self.scene_count=0;self.input_count=0
        self.respawns=0;self.first_pose=None;self.maximum_movement=0;self.native_drives=0;self.native_fires=0
        self.active_beam_scenes=0
        self.health_by_sequence={}
        self.health_by_motion={}
        self.scene_queue=queue.Queue(maxsize=1);self.coalesced_scenes=0
        self.discarded_scenes=queue.Queue()
        self.delivery_timing={};self.native_delivery_timing={};self.host_frame_timing={};self.native_threads={};self.host_ai_stats={}
        self.coalesced_motion=0
        self.save_lock=threading.Lock();self.checkpoint_waiters={}
        host=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                registered=False;sender=None;done=threading.Event()
                try:
                    self.request.settimeout(5)
                    self.request.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    hello=read_message(self.rfile,4096)
                    if not isinstance(hello,dict) or set(hello)!={'type','token','gameHash','contentHash','mods','presentationVersion','nativeVersion'} or hello['type']!='hello':
                        raise ValueError('Invalid hello')
                    if not isinstance(hello['token'],str) or not secrets.compare_digest(hello['token'],host.token):raise ValueError('Authentication failed')
                    if hello['gameHash']!=GAME_HASH or hello['contentHash']!=host.content or hello['mods']!=[]:raise ValueError('Content mismatch')
                    if type(hello['presentationVersion']) is not int or hello['presentationVersion']!=PRESENTATION_VERSION:raise ValueError('Update both multiplayer launchers: presentation protocol differs')
                    if type(hello['nativeVersion']) is not int or hello['nativeVersion']!=NATIVE_VERSION:raise ValueError('Update both multiplayer launchers: native protocol differs')
                    if host.failures:raise ValueError('Host native session failed: '+host.failures[0])
                    with host.lock:
                        if host.peer is not None:raise ValueError('Remote seat occupied')
                        host.peer=self.request;registered=True
                    host.game.script.exports_sync.setremotecontrol(True)
                    send_message(self.wfile,{'type':'welcome','faction':20008,'ident':PILOT,'nextSeq':host.previous_input+1,'presentationVersion':PRESENTATION_VERSION,'nativeVersion':NATIVE_VERSION,'sharedExploration':host.shared_exploration})
                    def publish():
                        previous=-1;previous_motion=0;last_ping=0
                        try:
                            while not done.is_set() and not host.stopped.is_set():
                                if host.failures:
                                    send_message(self.wfile,{'type':'error','message':'Host native session failed: '+host.failures[0]})
                                    done.set()
                                    self.request.shutdown(socket.SHUT_RDWR)
                                    break
                                with host.lock:scene=host.latest;motion=host.latest_motion
                                if motion and motion['seq']>previous_motion:
                                    if previous_motion:host.coalesced_motion+=max(0,motion['seq']-previous_motion-1)
                                    send_message(self.wfile,motion,host.delivery_timing,'motion');previous_motion=motion['seq']
                                if scene and scene['seq']>previous:
                                    send_message(self.wfile,scene,host.delivery_timing,'scene');previous=scene['seq']
                                elif time.monotonic()-last_ping>1:
                                    send_message(self.wfile,{'type':'ping'});last_ping=time.monotonic()
                                done.wait(0.005)
                        except (OSError,ValueError):done.set()
                    sender=threading.Thread(target=publish,daemon=True);sender.start()
                    rate_start=time.monotonic();frames=0
                    while not done.is_set() and not host.stopped.is_set():
                        message=read_message(self.rfile,65536)
                        now=time.monotonic()
                        if now-rate_start>=1:rate_start=now;frames=0
                        frames+=1
                        if frames>120:raise ValueError('Input rate limit')
                        if message=={'type':'pong'}:continue
                        command=control(message,host.previous_input)
                        host.previous_input=command['seq'];host.input_count+=1
                        host.game.script.post({'type':'repopulated-stream','payload':{'kind':'input','value':command}})
                except (EOFError,OSError) as error:
                    if registered and not host.stopped.is_set() and len(host.peer_disconnects)<32:host.peer_disconnects.append(str(error))
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
                'worldStream':True,'interestIdent':PILOT,'interestRadius':2500,'dynamicInterest':True,'streamMotion':True,'skipHealth':True,'streamPresentation':True,
                'sampleIntervalMs':250,'diagnosticSamples':False,'maxSamples':1000000,'rollingWorld':False,'pruneSceneFiles':True,'testRemoteDeath':test_respawn,'syncCampaignMap':True,
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
            if record.get('type')=='actor-control-ai-stats':
                self.host_ai_stats={key:record[key] for key in ('total','nativeForwarded','remoteDispatch','fallback','originalCalls')}
            if record.get('type')=='native-motion':
                try:
                    started=time.perf_counter()
                    frame={key:record[key] for key in ('seq','sourceTimeMs','simTimeMs','poses','inputSeq','inputTick','blocks','projectiles','movers','health')};frame['type']='motion'
                    validate_motion(frame)
                    self.delivery_timing.setdefault('motionValidation',DeliveryTiming()).record((time.perf_counter()-started)*1000)
                    self.native_delivery_timing=record.get('deliveryTiming',{})
                    if 'hostFrameTiming' in record:self.host_frame_timing=host_frame_timing_snapshot(record['hostFrameTiming'])
                    self.native_threads=record.get('nativeThreads',{})
                    with self.lock:self.latest_motion=frame
                    if record.get('pilotHealth'):
                        self.health_by_motion[frame['seq']]=record['pilotHealth']
                        while len(self.health_by_motion)>128:self.health_by_motion.pop(next(iter(self.health_by_motion)))
                    self.motion_count+=1
                except Exception as error:
                    if len(self.failures)<32:self.failures.append(str(error))
                return
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
                # Frida dispatches all sessions' callbacks and RPC replies on
                # one event thread. Parsing a large scene here starves input
                # and even prevents applyworld RPC replies from completing.
                try:self.scene_queue.put_nowait(record)
                except queue.Full:
                    try:self.discarded_scenes.put_nowait(self.scene_queue.get_nowait());self.coalesced_scenes+=1
                    except queue.Empty:pass
                    try:self.scene_queue.put_nowait(record)
                    except queue.Full:pass
            except Exception as error:
                if len(self.failures)<32:self.failures.append(str(error))
        def prepare_scenes():
          private_folder=folder.resolve()
          def prune(record):
            if not config.get('pruneSceneFiles'):return
            for key in ('path','visualPath','mapPath','remoteMapPath'):
                exported=Path(record[key]).resolve()
                if exported.parent!=private_folder:raise ValueError('Scene export path escaped private session')
                exported.unlink(missing_ok=True)
          while not self.stopped.is_set():
            try:record=self.scene_queue.get(timeout=0.1)
            except queue.Empty:continue
            try:
                while True:
                    try:prune(self.discarded_scenes.get_nowait())
                    except queue.Empty:break
                data,visual_data,map_data,remote_data=(Path(record[key]).read_bytes() for key in ('path','visualPath','mapPath','remoteMapPath'))
                prune(record)
                visuals=json.loads(visual_data)
                campaign_map=json.loads(map_data)
                remote_map=json.loads(remote_data)
                expected='shared' if self.shared_exploration else 'independent'
                if campaign_map['exploration']!=expected or remote_map['exploration']!=expected:raise ValueError('Native exploration policy differs')
                self.latest_host_map=campaign_map
                campaign_map=visible_map(remote_map)
                prepared_at=time.perf_counter()
                prepared=self.scene_worker.request('snapshot',data=data,seq=record['seq'],roots=record['roots'],visuals=visuals,map=campaign_map)
                scene,pose=prepared['scene'],prepared['pose']
                self.delivery_timing.setdefault('scenePreparation',DeliveryTiming()).record(prepared['scenePreparationMs'])
                self.delivery_timing.setdefault('sceneWorkerRoundtrip',DeliveryTiming()).record((time.perf_counter()-prepared_at)*1000)
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
        self.scene_worker=None
        try:
            self.scene_worker=SceneWorker('host')
            self.game=(session_factory or NativeSession)(exe,folder,config,native_message,campaign=True,campaign_source=campaign_source)
            if self.failures:raise RuntimeError('Host native instrumentation failed: '+self.failures[0])
        except Exception:
            try:
                if getattr(self,'game',None):self.game.close()
            finally:
                if self.scene_worker:self.scene_worker.close()
                self.server.server_close()
            raise
        self.scene_thread=threading.Thread(target=prepare_scenes,daemon=True);self.scene_thread.start()
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

    def delivery_summary(self):
        return {'native':self.native_delivery_timing,'python':{name:value.summary() for name,value in tuple(self.delivery_timing.items())},
                'frameTiming':frame_timing(self.host_frame_timing),'threads':self.native_threads,'coalescedMotion':self.coalesced_motion,'hostAI':self.host_ai_stats}

    def frame_timing_snapshot(self):
        return host_frame_timing_snapshot(self.host_frame_timing) if self.host_frame_timing else {}

    def close(self):
        with self.save_lock:self._close()

    def _close(self):
        self.stopped.set()
        with self.lock:
            if self.peer:
                try:self.peer.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.scene_thread.join(timeout=2)
        self.scene_worker.close()
        self.game.close()


def client_presentation_delay(native_campaign,requested=100):
    if type(requested) is not int or not 0<=requested<=200:raise ValueError('Invalid presentation delay')
    return requested if native_campaign else 0


def run_client(exe,folder,address,token,seconds=3600,test_controls=False,stop_event=None,native_campaign=True,
               control_scheme='MOUSE_ROT',notice_callback=None,health_validator=None,progress_callback=None,test_menus=False,presentation_delay_ms=100,session_factory=None):
    if control_scheme not in ('MOUSE_ROT','KEY_ROT','CARDINAL'):raise ValueError('Unknown native control scheme')
    presentation_delay_ms=client_presentation_delay(native_campaign,presentation_delay_ms)
    game=None;scene_worker=None;reader=None;workers=[];stopped=threading.Event();outbound=queue.Queue(maxsize=1);pong_pending=threading.Event()
    scenes=queue.Queue(maxsize=1);motions=queue.Queue(maxsize=1);applied_worlds=queue.Queue();motion_received=0
    delivery_timing={};coalesced_motion=0;coalesced_geometry=0
    failures=[];updates=[];sequence=0;pilot_pose=[3000,3000,0,0,0];controls_started=None;received_beam_scenes=0
    native_progress_at=None;native_display_at=None;native_display_frames=0;native_motion_at=None;native_motion_frames=0;native_last_stage=None;native_progress_signature=None
    sock=socket.create_connection(address,timeout=10);sock.settimeout(10)
    sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    stream=sock.makefile('rwb');started=time.monotonic()
    try:
        send_message(stream,{'type':'hello','token':token,'gameHash':GAME_HASH,'contentHash':content_hash(exe),'mods':[],'presentationVersion':PRESENTATION_VERSION,'nativeVersion':NATIVE_VERSION})
        welcome=read_message(stream,4096)
        if welcome.get('type')!='welcome' or welcome.get('ident')!=PILOT or welcome.get('faction')!=20008 or welcome.get('presentationVersion')!=PRESENTATION_VERSION or welcome.get('nativeVersion')!=NATIVE_VERSION or type(welcome.get('sharedExploration')) is not bool:
            raise ValueError('Server refused native client: '+str(welcome))
        sequence=welcome['nextSeq']
        first,initial,pilot_pose=read_initial_world(sock,stream)
        latest_seq=first['seq'];pending=None
        def native_message(record):
            nonlocal sequence,pending,controls_started,native_progress_at,native_display_at,native_display_frames,native_motion_at,native_motion_frames,native_last_stage,native_progress_signature
            kind=record.get('type')
            if kind=='error':failures.append(record.get('description','Native error'))
            if kind=='native-stage':native_last_stage=record
            if kind in ('native-progress','world-applied') and record.get('motionApplied',0)>native_motion_frames:
                native_motion_frames=record['motionApplied'];native_motion_at=time.monotonic()
            if kind=='native-progress':
                # The independent renderer can keep swapping a frozen world.
                # Only update/application progress refreshes this watchdog.
                signature=(record.get('nativeZoneUpdates',0),record.get('motionApplied',0))
                if signature!=native_progress_signature and any(signature):native_progress_at=time.monotonic()
                native_progress_signature=signature
                if record.get('displayFrames',0)>native_display_frames:
                    native_display_frames=record['displayFrames'];native_display_at=time.monotonic()
                if record.get('stages'):native_last_stage=record['stages']
            if kind in ('world-applied','native-navigation-intent','native-pilot-input','native-menu-test'):
                native_progress_at=time.monotonic()
            if kind=='native-client-notice' and notice_callback:notice_callback(record['message'])
            if kind=='world-applied':
                if record.get('displayFrames',0)>native_display_frames:
                    native_display_frames=record['displayFrames'];native_display_at=time.monotonic()
                updates.append(record)
                if progress_callback:progress_callback(record)
                if health_validator:
                    try:health_validator(record)
                    except Exception as error:failures.append('Native pilot health check failed: '+str(error))
                if record['verifiedRoots']!=record['expectedRoots']:failures.append('Native replica root count differs')
                applied_worlds.put_nowait((record,pending))
            if native_campaign and kind=='native-navigation-intent':
                if record['ident']!=PILOT:return
                if controls_started is None:controls_started=time.monotonic()
                message={'type':'input','seq':sequence,'action':'native','dimensions':record['dimensions'],
                         'destination':record['destination'],'precision':record['precision'],'weapons':record['weapons'],'viewRadius':record['viewRadius'],'clientTick':record.get('clientTick',0)}
                sequence+=1
                if test_controls:
                    held=time.monotonic()-controls_started<4
                    message.update(dimensions=0x106,destination=[0,0,200 if held else 0,0,0,0])
                    message['weapons']=[[row[0],features&0x800008e0 if held else 0,1000,0,0,0,0,0] for row,features in zip(record['weapons'],record['weaponFeatures'])]
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
                try:messages=coalesce_inputs(outbound.get_nowait(),messages)
                except queue.Empty:pass
                try:outbound.put_nowait(messages)
                except queue.Full:pass
        config={'replica':True,'worldReplica':True,'persistentReplica':True,'renderOnly':True,'pilotInput':True,'followPilot':PILOT,'skipHealth':True,'predictPresentation':True,
                'measureRendering':test_controls,'replicatePresentation':True,
                'frameLimit':60,'fastMotion':True,'fullReplicaReadback':False,'sampleIntervalMs':100,'diagnosticSamples':False,'maxSamples':1000000,'verifyPilotHealth':health_validator is not None,
                'presentationDelayMs':presentation_delay_ms}
        if native_campaign:config.update(nativeCampaignClient=True,sceneIdleGate=True,captureNativeIntent=True,controlScheme=control_scheme,syncCampaignMap=True,testNativeMenus=test_menus,sharedExploration=welcome['sharedExploration'])
        game=(session_factory or NativeSession)(exe,folder,config,native_message,campaign=native_campaign,bootstrap=initial)
        scene_worker=SceneWorker('client',first['seq'])
        def receive():
            nonlocal latest_seq,motion_received,coalesced_motion,coalesced_geometry
            previous_motion=0;previous_source_time=0;previous_sim_time=-1;previous_received=0
            def latest(queue_,value):
                nonlocal coalesced_motion,coalesced_geometry
                try:queue_.put_nowait(value)
                except queue.Full:
                    try:
                        queue_.get_nowait()
                        if queue_ is motions:coalesced_motion+=1
                        else:coalesced_geometry+=1
                    except queue.Empty:pass
                    try:queue_.put_nowait(value)
                    except queue.Full:pass
            try:
                while not stopped.is_set():
                    game.network_stage='read'
                    scene=read_message(stream,WIRE_LIMIT)
                    if scene.get('type')=='error':raise RuntimeError(scene.get('message','Host native session failed'))
                    if scene.get('type')=='motion':
                        received=time.monotonic();received_at=time.time()*1000
                        if previous_received:delivery_timing.setdefault('receiveInterval',DeliveryTiming()).record((received-previous_received)*1000)
                        previous_received=received;validated_at=time.perf_counter()
                        validate_motion(scene,previous_motion,previous_source_time,previous_sim_time)
                        if previous_source_time:delivery_timing.setdefault('sourceInterval',DeliveryTiming()).record(scene['sourceTimeMs']-previous_source_time)
                        delivery_timing.setdefault('motionValidation',DeliveryTiming()).record((time.perf_counter()-validated_at)*1000)
                        previous_motion=scene['seq'];previous_source_time=scene['sourceTimeMs'];previous_sim_time=scene['simTimeMs'];motion_received+=1
                        scene['receivedAt']=received_at # Local metadata, added after strict wire validation.
                        latest(motions,scene)
                        continue
                    if scene.get('type')=='ping':
                        # Heartbeats must survive flight-state coalescing and
                        # menus that temporarily stop producing player input.
                        pong_pending.set()
                        continue
                    if scene.get('type')!='scene' or type(scene.get('seq')) is not int or not latest_seq<scene['seq']<=10000000:
                        raise ValueError('Invalid scene sequence')
                    latest_seq=scene['seq'];latest(scenes,scene)
            except Exception as error:
                if not stopped.is_set():failures.append(str(error))
        def apply_motions():
            try:
                while not stopped.is_set():
                    try:frame=motions.get(timeout=0.1)
                    except queue.Empty:continue
                    delivery_timing.setdefault('receiveToPost',DeliveryTiming()).record(time.time()*1000-frame['receivedAt'])
                    game.motion_stage=frame['seq'];game.script.post({'type':'repopulated-stream','payload':{'kind':'motion','value':frame}})
            except Exception as error:
                if not stopped.is_set():failures.append(str(error))
        def apply_scenes():
            nonlocal pilot_pose,pending,received_beam_scenes
            previous=first['seq']
            def acknowledge(record,consumed):
                if consumed:
                    if record['clusters']<0:failures.append('Incremental native apply failed: '+str(record['clusters']))
                    else:scene_worker.request('commit',seq=record['seq'])
                    if native_campaign:
                        verify=Path(record['campaignMap']['verifiedPath'])
                        try:
                            actual=json.loads(verify.read_text());expected=consumed['map']
                            if actual['exploration']!=expected['exploration'] or actual['radius']!=expected['radius'] or actual['width']!=expected['width'] or actual['regions']!=expected['regions'] or [r[:3] for r in actual['cells']]!=[r[:3] for r in expected['cells']] or actual['objectives']!=expected['objectives']:
                                (game.folder/('map-mismatch-'+str(record['seq'])+'.json')).write_text(json.dumps({'actual':actual,'expected':expected}))
                                raise ValueError('Authoritative native map differs after apply')
                        finally:verify.unlink(missing_ok=True)
                        record['campaignMap'].pop('verifiedPath',None)
                    try:consumed['path'].unlink(missing_ok=True)
                    except OSError:pass
                if record.get('verifiedPath'):
                    try:Path(record['verifiedPath']).unlink(missing_ok=True)
                    except OSError:pass
            try:
                while not stopped.is_set():
                    if pending is not None:
                        try:record,consumed=applied_worlds.get(timeout=0.1)
                        except queue.Empty:continue
                        acknowledge(record,consumed);pending=None;continue
                    try:scene=scenes.get(timeout=0.1)
                    except queue.Empty:continue
                    # Geometry parsing and native RPCs must not delay newer
                    # kinematics or heartbeat delivery from the socket reader.
                    prepared_at=time.perf_counter()
                    prepared=scene_worker.request('prepare',scene=scene,diagnostics=getattr(game,'diagnostic_history',None) is not None);previous=scene['seq'];pilot_pose=prepared['pose'];plan=prepared['plan']
                    delivery_timing.setdefault('sceneWorkerRoundtrip',DeliveryTiming()).record((time.perf_counter()-prepared_at)*1000)
                    delivery_timing.setdefault('sceneDecode',DeliveryTiming()).record(prepared['sceneDecodeMs'])
                    delivery_timing.setdefault('scenePlan',DeliveryTiming()).record(prepared['scenePlanMs'])
                    if prepared['hasBeams']:received_beam_scenes+=1
                    path=game.folder/f"network-world-{scene['seq']}.lua"
                    if plan['additions'] is not None:path.write_bytes(plan['additions'])
                    pending={'path':path,'plan':plan,'map':scene['map']}
                    game.network_stage='scene '+str(scene['seq'])
                    game.script.post({'type':'repopulated-stream','payload':{'kind':'world','value':{'seq':scene['seq'],'path':str(path),'roots':scene['roots'],'sha256':scene['sha256'],
                                                         'visuals':pack_visuals(scene['visuals']),
                                                         'incremental':{key:plan[key] for key in ('remove','poses','health','retained','replaced','continuity','transitions') if key in plan},
                                                         'hasAdditions':plan['additions'] is not None,
                                                         'map':pack_map(scene['map']) if native_campaign else None,
                                                         'mapSummary':map_summary(scene['map']) if native_campaign else None}}})
            except Exception as error:
                if not stopped.is_set():failures.append(str(error))
        workers=[threading.Thread(target=target,daemon=True) for target in (apply_motions,apply_scenes)]
        for worker in workers:worker.start()
        reader=threading.Thread(target=receive,daemon=True);reader.start();game.resume();resumed_at=time.monotonic()
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline and not failures and not (stop_event and stop_event.is_set()):
            now=time.monotonic()
            if native_progress_at is not None and now-native_progress_at>8:
                failures.append('Native client stopped progressing for eight seconds; last native stage: '+str(native_last_stage));break
            if native_display_at is not None and now-native_display_at>8:
                failures.append('Native client stopped presenting new frames for eight seconds');break
            if native_motion_at is not None and now-native_motion_at>8:
                failures.append('Native client stopped applying authoritative motion for eight seconds; last native stage: '+str(native_last_stage));break
            if native_progress_at is None and now-resumed_at>20:
                failures.append('Native client did not begin progressing within twenty seconds');break
            if native_motion_at is None and now-resumed_at>20:
                failures.append('Native client did not apply authoritative motion within twenty seconds');break
            if pong_pending.is_set():
                pong_pending.clear();send_message(stream,{'type':'pong'})
            try:messages=outbound.get(timeout=0.1)
            except queue.Empty:continue
            for message in messages:send_message(stream,message)
        return {'updates':len(updates),'displayFrames':max((m.get('displayFrames',0) for m in updates),default=0),
                'pilotRenderCalls':max((m.get('pilotRenderCalls',0) for m in updates),default=0),
                'eventPollCalls':max((m.get('pollCalls',0) for m in updates),default=0),
                'nativeInputMessages':game.message_counts['native-navigation-intent' if native_campaign else 'native-pilot-input'],
                'nativeCampaignClient':native_campaign,
                'controlScheme':control_scheme,
                'failures':failures,'lastPilotPose':pilot_pose,'testControls':test_controls,
                'frameTiming':frame_timing(updates[-1].get('frameTiming',{})) if updates else {},
                'framePacing':updates[-1].get('framePacing',{}) if updates else {},
                'privateField':updates[-1].get('privateField',{}) if updates else {},
                'ownership':updates[-1].get('ownership',{}) if updates else {},
                'localExhaust':updates[-1].get('localExhaust',{}) if updates else {},
                'replacement':updates[-1].get('replacement',{}) if updates else {},
                'sceneGate':updates[-1].get('sceneGate',{}) if updates else {},
                'sceneHandoff':updates[-1].get('sceneHandoff',{}) if updates else {},
                'motionAdmission':updates[-1].get('motionAdmission',{}) if updates else {},
                'sceneIdleGate':scene_gate_enabled(game.messages,updates[-1].get('sceneGate',{}) if updates else {}),
                'predictedFrames':max((m.get('predictedFrames',0) for m in updates),default=0),
                'motionFramesApplied':max((m.get('motionApplied',0) for m in updates),default=0),
                'motionFramesReceived':motion_received,
                'motionTimeline':updates[-1].get('motionTimeline',{}) if updates else {},
                'simulationPresentationRate':updates[-1].get('simulationPresentationRate') if updates else None,
                'presentationBusy':updates[-1].get('presentationBusy',{}) if updates else {},
                'presentationDelayMs':updates[-1].get('presentationDelayMs',presentation_delay_ms) if updates else presentation_delay_ms,
                'interpolation':updates[-1].get('interpolation',{}) if updates else {},
                'visualFrameSeq':updates[-1].get('visualFrameSeq',0) if updates else 0,
                'visualFrameApplications':updates[-1].get('visualFrameApplications',0) if updates else 0,
                'preparedVisualFrames':updates[-1].get('preparedVisualFrames',0) if updates else 0,
                'visualHistoryResets':updates[-1].get('visualHistoryResets',0) if updates else 0,
                'presentationClock':updates[-1].get('presentationClock',{}) if updates else {},
                'deliveryTiming':{'native':updates[-1].get('deliveryTiming',{}) if updates else {},
                                  'python':{name:value.summary() for name,value in tuple(delivery_timing.items())},
                                  'coalescedMotion':coalesced_motion,'coalescedGeometry':coalesced_geometry},
                'prediction':updates[-1].get('prediction',{}) if updates else {},
                'nativeZoneUpdates':updates[-1].get('nativeZoneUpdates',0) if updates else 0,
                'nativeThreads':{key:updates[-1].get(key) if updates else None for key in ('drawThread','swapThread','cameraThread','nativeUpdateThread')},
                'lastNativeStage':native_last_stage,
                'presentation':updates[-1].get('presentation',{}) if updates else {},'receivedBeamScenes':received_beam_scenes,
                'persistentReplica':updates[-1].get('persistentReplica',{}) if updates else {},
                'pilotLifetimes':pilot_lifetimes(updates),'map':updates[-1].get('campaignMap',{}) if updates else {},
                'nativeMenuTests':[m for m in game.messages if m.get('type')=='native-menu-test']}
    finally:
        stopped.set()
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        if reader:reader.join(timeout=2)
        for worker in workers:worker.join(timeout=2)
        if scene_worker:scene_worker.close()
        try:stream.close()
        except OSError:pass
        sock.close()
        if game:game.close()


def scene_gate_enabled(messages,stats=None):
    """Report native configuration evidence, independently of requested flags."""
    if any(message.get('type')=='native-scene-gate' and message.get('action')=='configured' for message in messages):return True
    state=(stats or {}).get('state')
    return type(state) in (int,float) and state in (1,2,3,4)


def pilot_lifetimes(updates):
    runs=[]
    for message in updates:
        pointer=message.get('pilotPointer')
        if not pointer:continue
        if runs and runs[-1]['pointer']==pointer:runs[-1]['updates']+=1
        else:runs.append({'pointer':pointer,'firstSequence':message['seq'],'updates':1})
    return {'lifetimes':len(runs),'longestRetainedUpdates':max((r['updates'] for r in runs),default=0),'changes':runs}


def host_frame_timing_snapshot(timing):
    """Retain the native full histogram, never infer FPS from motion samples."""
    histogram=timing.get('histogram') if isinstance(timing,dict) else None
    if not isinstance(histogram,list) or len(histogram)!=251 or any(type(value) is not int or value<0 for value in histogram):
        raise ValueError('Invalid native host frame histogram')
    long_frames=timing.get('longFrames');maximum=timing.get('maxFrameMs')
    if type(long_frames) is not int or long_frames<0 or type(maximum) not in (int,float) or not math.isfinite(maximum) or maximum<0:
        raise ValueError('Invalid native host frame timing')
    return {'histogram':list(histogram),'longFrames':long_frames,'maxFrameMs':maximum}


def frame_timing(timing):
    histogram=timing.get('histogram',[]);count=sum(histogram)
    threshold=count*0.95;seen=0;p95=None
    for ms,number in enumerate(histogram):
        seen+=number
        if seen>=threshold and count:p95=ms;break
    return {'sampledFrames':count,'p95Ms':p95,'framesOver50Ms':timing.get('longFrames',0),'maxMs':timing.get('maxFrameMs',0)}


class CheckRecording:
    """Record the existing combat checks without replacing their assertions."""
    def __init__(self,seconds,report_path,recorder=None,*,scene_idle_gate=False,record_delay_seconds=0):
        if type(scene_idle_gate) is not bool:raise ValueError('Scene idle gate must be a private boolean fixture')
        if type(record_delay_seconds) is not int or not 0<=record_delay_seconds<=3600:raise ValueError('Recording delay must be 0–3600 whole seconds')
        if recorder is None:
            from record_native_pair import NativePairRecorder
            recorder=NativePairRecorder(seconds)
        self.recorder=recorder;self.sessions=[];self.stop=threading.Event();self.thread=None;self.errors=[];self.record_delay_seconds=record_delay_seconds
        self.recorder.metadata.update(testOutcome='in-progress',testReport=str(report_path),testMode='native cooperative check',sceneIdleGateRequested=scene_idle_gate,sceneIdleGate=False,recordDelaySeconds=record_delay_seconds)
        self.recorder._save()
        sessions=self.sessions;metadata=self.recorder.metadata;save_metadata=self.recorder._save
        class TrackedNativeSession(NativeSession):
            def __init__(self,exe,folder,config,*args,**kwargs):
                config=dict(config,measureRendering=True,measureMotion=True,
                            windowTitle='Reassembly — Repopulated Live '+('Client' if config.get('replica') else 'Host'))
                if scene_idle_gate and config.get('replica'):
                    if not config.get('nativeCampaignClient'):raise ValueError('Scene idle gate requires the native campaign client')
                    config['testSceneIdleGate']=True
                self.recording_is_client=bool(config.get('replica'))
                if self.recording_is_client:
                    metadata['sceneIdleGateRequested']=config.get('sceneIdleGate') is True or config.get('testSceneIdleGate') is True
                    save_metadata()
                super().__init__(exe,folder,config,*args,**kwargs)
                sessions.append(self)
        self.session_factory=TrackedNativeSession

    def begin(self,host_game):
        def monitor():
            try:
                ready_at=None;ready_client=None
                while not self.stop.wait(0.05):
                    clients=[session for session in tuple(self.sessions) if session is not host_game]
                    if clients and clients[-1].message_counts['world-applied']>=3:
                        now=time.monotonic();client=clients[-1]
                        if ready_at is None or client is not ready_client:
                            ready_at=now;ready_client=client
                            self.recorder.metadata.update(nativeReadyAtMs=time.time_ns()//1000000,nativeReadyHostPid=host_game.pid,nativeReadyClientPid=client.pid)
                            self.recorder._save()
                        if now-ready_at>=self.record_delay_seconds:
                            self.recorder.metadata.update(recordStartRequestedAtMs=time.time_ns()//1000000,actualRecordDelaySeconds=now-ready_at)
                            self.recorder._save()
                            self.recorder.start(host_game.pid,client.pid);return
            except Exception as error:self.errors.append(str(error))
        self.thread=threading.Thread(target=monitor,name='coop-check-recorder',daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():raise RuntimeError('Native pair recorder did not finish startup')
        clients=[session for session in self.sessions if getattr(session,'recording_is_client',False)]
        actual=False
        for client in clients:
            messages=getattr(client,'messages',())
            stats=next((message.get('sceneGate',{}) for message in reversed(messages) if message.get('type')=='world-applied'),{})
            actual=actual or scene_gate_enabled(messages,stats)
        self.recorder.metadata['sceneIdleGate']=actual
        self.recorder.close()

    def require_complete(self):
        self.close()
        if self.errors:raise RuntimeError('Native pair recording failed: '+self.errors[0])
        if not self.recorder.metadata.get('complete'):raise RuntimeError('Native pair recording did not produce both complete videos')

    def finalize(self,error=None):
        try:self.close()
        except Exception as cleanup_error:
            if error is None:error=cleanup_error
        if self.errors and error is None:error=RuntimeError(self.errors[0])
        if not self.recorder.metadata.get('complete') and error is None:error=RuntimeError('Native pair capture incomplete')
        self.recorder.metadata['testOutcome']='failed' if error else 'passed'
        if error:self.recorder.metadata['testError']=str(error)
        self.recorder._save()


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
    parser.add_argument('--presentation-delay-ms',type=int,default=100,help='Native campaign presentation buffer, 0–200 ms (default 100); older sandbox clients use 0')
    parser.add_argument('--record',action='store_true',help='In check mode, record both native game windows after three world applications')
    parser.add_argument('--record-seconds',type=int,default=40,help='Bounded recording duration, 5–120 seconds (default 40)')
    parser.add_argument('--record-delay-seconds',type=int,default=0,help='Wait 0–3600 seconds after native readiness before recording; default 0')
    parser.add_argument('--scene-idle-gate',action='store_true',help='Check-only alias for the native client gate (enabled by default); requires --record and the native campaign client')
    args=parser.parse_args()
    if not 1<=args.port<=65535 or not 5<=args.seconds<=3600:parser.error('Invalid port or duration')
    if args.check_respawn and (args.mode!='check' or args.seconds<10):parser.error('Respawn check requires check mode and at least ten seconds')
    if args.check_backpressure and (args.mode!='check' or args.seconds<10):parser.error('Backpressure check requires check mode and at least ten seconds')
    if args.check_health and args.mode!='check':parser.error('Health check requires check mode')
    if not 0<=args.presentation_delay_ms<=200:parser.error('Presentation delay must be 0–200 milliseconds')
    if not 5<=args.record_seconds<=120:parser.error('Recording duration must be 5–120 seconds')
    if not 0<=args.record_delay_seconds<=3600:parser.error('Recording delay must be 0–3600 seconds')
    if args.record_delay_seconds and not args.record:parser.error('Recording delay requires --record')
    if args.record and (args.mode!='check' or args.record_delay_seconds+args.record_seconds>args.seconds-5):parser.error('Recording requires check mode with at least five additional test seconds after its delay and duration')
    if args.scene_idle_gate and (args.mode!='check' or not args.record or not args.native_campaign_client):
        parser.error('Scene idle gate requires recorded check mode with the native campaign client')
    root=Path(__file__).resolve().parents[1]/'.runtime';stamp=str(time.time_ns())
    report_path=root/('coop-check-'+stamp+'.json')
    host=None;recording=None;result=None;check_error=None
    try:
        if args.record:recording=CheckRecording(args.record_seconds,report_path,scene_idle_gate=args.scene_idle_gate,record_delay_seconds=args.record_delay_seconds)
        if args.mode in ('host','check'):
            host=CoopHost(args.exe,root/('coop-host-'+stamp),(args.address,args.port),args.token,test_respawn=args.check_respawn,
                          hold_controls_ms=4000 if args.check_backpressure else 0,test_beams=args.check_beams,verify_health=args.check_health,test_menus=args.check_host_menus,shared_exploration=args.shared_exploration,
                          session_factory=recording.session_factory if recording else None)
            if recording:recording.begin(host.game)
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
            if record.get('healthFrameSeq') and record.get('healthAudit'):
                audit=record['healthAudit']
                if audit['mismatches']:raise ValueError('Realtime block health differs: '+str(audit['maximumError']))
                if not audit['blocksCompared']:return
                health_checks['snapshotsCompared']+=1;health_checks['blocksCompared']+=audit['blocksCompared']
                health_checks['maximumHealthError']=max(health_checks['maximumHealthError'],audit['maximumError']);return
            with host.lock:expected=host.health_by_motion.get(record.get('healthFrameSeq')) if record.get('healthFrameSeq') else host.health_by_sequence.get(record['seq'])
            if not expected:raise ValueError('Matching authoritative health sample unavailable')
            report=audit_health(expected,record['pilotHealth'])
            if not report['nativeHealthMultisetsEquivalent']:raise ValueError('Runtime damage differs: '+str(report['maximumHealthError']))
            health_checks['snapshotsCompared']+=1;health_checks['blocksCompared']+=report['blocksCompared']
            health_checks['maximumHealthError']=max(health_checks['maximumHealthError'],report['maximumHealthError'])
        result=run_client(args.exe,root/('coop-client-'+stamp),(args.address,args.port),host.token if host else args.token,
                          args.seconds,test_controls=args.mode=='check',native_campaign=args.native_campaign_client,control_scheme=args.control_scheme,
                          health_validator=validate_health if args.check_health else None,test_menus=args.check_map_menus,
                          presentation_delay_ms=args.presentation_delay_ms,session_factory=recording.session_factory if recording else None)
        if recording:result.update(recordingFolder=str(recording.recorder.folder),recordDelaySeconds=args.record_delay_seconds)
        if args.check_health:result['healthValidation']=health_checks
        if host:
            deadline=time.monotonic()+3
            while host.peer is not None and time.monotonic()<deadline:time.sleep(0.02)
            result.update(hostScenes=host.scene_count,hostInputs=host.input_count,hostFailures=host.failures,
                          hostDeliveryTiming=host.delivery_summary(),
                          hostFrameTiming=host.frame_timing_snapshot(),
                          hostActiveBeamScenes=host.active_beam_scenes,coalescedScenes=host.coalesced_scenes,
                          nativeDrives=host.native_drives,nativeFires=host.native_fires,maximumMovement=host.maximum_movement,
                          remoteRespawns=host.respawns,rejectedPeers=len(host.rejections),rejectionReasons=host.rejections,peerDisconnects=host.peer_disconnects,campaignRemoteControlValidated=False,playableMultiplayerValidated=False)
            result['controlQueue']=host.game.script.exports_sync.controlstats()
            result['hostMenuTests']=[m for m in host.game.messages if m.get('type')=='host-menu-test']
            result['campaignRemoteControlValidated']=not result['failures'] and not host.failures and result['updates']>=3 and \
                result['pilotRenderCalls']>=10 and host.native_drives>=10 and host.native_fires>=1 and host.maximum_movement>=20
        report_path.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
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
            if [(m['action'],m['tab']) for m in events]!=[('opened',1),('closed',1),('opened',16),('closed',16)] or any(events[i+1]['displayFrames']-events[i]['displayFrames']<60 or events[i+1]['menuSnapshotApplies']-events[i]['menuSnapshotApplies']<2 or events[i+1]['motionApplied']-events[i]['motionApplied']<3 for i in (0,2)):
                raise RuntimeError('Native map/binding open, render, close cycle was not verified')
        if args.check_host_menus:
            events=result['hostMenuTests']
            if [(m['action'],m['tab']) for m in events]!=[('opened',1),('closed',1),('opened',16),('closed',16)] or any(events[i+1]['hostMenuUpdates']-events[i]['hostMenuUpdates']<60 or events[i+1]['hostMenuBrakes']-events[i]['hostMenuBrakes']<30 or events[i+1]['hostSnapshots']-events[i]['hostSnapshots']<5 for i in (0,2)):
                raise RuntimeError('Host menu simulation continuity was not verified')
        if args.check_health and (health_checks['snapshotsCompared']<3 or not any(m.get('type')=='fixture-partial-damage-result' and m.get('result')==1 for m in host.game.messages)):
            raise RuntimeError('Native partial damage replication was not verified')
        if args.mode=='check' and (result['controlQueue']['playerControlled'] or not result['controlQueue']['vacantAICalls']):
            raise RuntimeError('Vacant faction AI takeover was not verified')
        if recording:recording.require_complete()
    except Exception as error:
        check_error=error;raise
    finally:
        try:
            if host:host.close()
        except Exception as error:
            check_error=error;raise
        finally:
            if recording:recording.finalize(check_error)
            if result is not None:
                result['testOutcome']='failed' if check_error else 'passed'
                if check_error:result['testError']=str(check_error)
                report_path.write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
