"""Local browser and LAN discovery for the experimental native two-player mode."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import threading
import time
from native_coop import CoopHost,run_client,content_hash
from native_checkpoints import Checkpoints,ID
from native_paths import STATE_ROOT
from coop_wire import GAME_HASH
from coop_wire import NATIVE_VERSION
from presentation_wire import PRESENTATION_VERSION

DISCOVERY_PORT=32917
QUERY=b'REPOPULATED_NATIVE_DISCOVER_1'
ROOT=Path(__file__).resolve().parents[1]


class Advertisement:
    def __init__(self,host,name,port,discovery_port=DISCOVERY_PORT):
        self.host=host;self.name=name;self.port=port;self.stop=threading.Event();self.server_id=secrets.token_hex(8)
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.socket.bind(('0.0.0.0',discovery_port));self.socket.settimeout(0.2)
        self.thread=threading.Thread(target=self.serve,daemon=True);self.thread.start()

    def serve(self):
        last={}
        while not self.stop.is_set():
            try:
                data,peer=self.socket.recvfrom(512)
                now=time.monotonic()
                if data!=QUERY or now-last.get(peer[0],0)<1:continue
                if len(last)>128:last.clear()
                last[peer[0]]=now
                with self.host.lock:occupied=self.host.peer is not None
                response={'type':'native-server','serverId':self.server_id,'name':self.name,'port':self.port,'gameHash':GAME_HASH,
                          'players':2 if occupied else 1,'maxPlayers':2,'stockOnly':True,
                          'nativeVersion':NATIVE_VERSION,'presentationVersion':PRESENTATION_VERSION}
                self.socket.sendto(json.dumps(response).encode(),peer)
            except socket.timeout:continue
            except OSError:break

    def close(self):
        self.stop.set();self.socket.close();self.thread.join(timeout=1)


def discover(discovery_port=DISCOVERY_PORT):
    found={}
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);udp.settimeout(0.15)
        for address in ('255.255.255.255','127.0.0.1'):
            udp.sendto(QUERY,(address,discovery_port))
        deadline=time.monotonic()+0.8
        while time.monotonic()<deadline and len(found)<64:
            try:
                raw,peer=udp.recvfrom(2048);message=json.loads(raw)
                if not isinstance(message,dict) or message.get('type')!='native-server':continue
                port=message.get('port')
                if type(port) is not int or not 1<=port<=65535:continue
                name=message.get('name')
                if not isinstance(name,str) or len(name)>80:continue
                server_id=message.get('serverId')
                if not isinstance(server_id,str) or len(server_id)!=16 or any(c not in '0123456789abcdef' for c in server_id):continue
                entry={'address':peer[0],'port':port,'name':name,'players':message.get('players'),
                       'compatible':message.get('gameHash')==GAME_HASH and message.get('nativeVersion')==NATIVE_VERSION
                                    and message.get('presentationVersion')==PRESENTATION_VERSION,'stockOnly':message.get('stockOnly') is True}
                if server_id not in found or peer[0]!='127.0.0.1':found[server_id]=entry
            except (socket.timeout,ValueError,UnicodeError):continue
    return list(found.values())


class Launcher:
    def __init__(self,exe,local_only=False):
        self.exe=exe;self.lock=threading.Lock();self.state='idle';self.error=None
        self.local_only=local_only
        self.worker=None;self.stop=threading.Event();self.host=None;self.result=None;self.name=None
        self.notice=None
        self.client_progress={}
        self.checkpoints=Checkpoints(STATE_ROOT/'native-checkpoints');self.save_worker=None;self.save_status=None;self.last_checkpoint=None

    def status(self):
        with self.lock:
            result={'state':self.state,'error':self.error,'result':self.result,'name':self.name,
                    'saveStatus':self.save_status,'lastCheckpoint':self.last_checkpoint,'notice':self.notice}
            if self.client_progress:
                result.update(self.client_progress)
                result['lastReplicaUpdateAge']=round(time.monotonic()-self.client_progress['lastReplicaUpdate'],1)
            if self.host:
                result.update(token=self.host.token,port=self.host.server.server_address[1],
                              scenes=self.host.scene_count,inputs=self.host.input_count,
                              remoteConnected=self.host.peer is not None)
                if self.host.latest_host_map:
                    from campaign_map_wire import map_summary
                    result['campaignMap']=map_summary(self.host.latest_host_map)
                result['sharedExploration']=self.host.shared_exploration
            return result

    def start(self,request):
        mode=request.get('mode');port=request.get('port',32916)
        if mode not in ('host','join') or type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid session settings')
        address=request.get('address','127.0.0.1');token=request.get('token','');name=request.get('name','Repopulated alpha')
        checkpoint=request.get('checkpoint')
        native_campaign=request.get('nativeCampaign',True);control_scheme=request.get('controlScheme','MOUSE_ROT')
        shared_exploration=request.get('sharedExploration')
        if shared_exploration is not None and type(shared_exploration) is not bool:raise ValueError('Invalid shared exploration setting')
        if type(native_campaign) is not bool or control_scheme not in ('MOUSE_ROT','KEY_ROT','CARDINAL'):raise ValueError('Invalid native control settings')
        if checkpoint is not None and (mode!='host' or not isinstance(checkpoint,str) or not ID.fullmatch(checkpoint)):raise ValueError('Invalid saved galaxy')
        if not isinstance(address,str) or not address.strip() or len(address)>253:raise ValueError('Invalid address')
        if mode=='join' and (not isinstance(token,str) or not 16<=len(token)<=128):raise ValueError('Enter the host join token')
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('Invalid server name')
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('Stop the current session first')
            self.stop=threading.Event();self.error=None;self.result=None;self.name=name;self.state='starting';self.save_status=None;self.notice=None;self.client_progress={}
            self.worker=threading.Thread(target=self.run,args=(mode,address.strip(),port,token,name,checkpoint,native_campaign,control_scheme,shared_exploration),daemon=True)
            self.worker.start()

    def run(self,mode,address,port,token,name,checkpoint=None,native_campaign=True,control_scheme='MOUSE_ROT',shared_exploration=None):
        host=None;advertisement=None
        folder=STATE_ROOT/('native-'+mode+'-'+str(time.time_ns()))
        try:
            if mode=='host':
                source=None
                if checkpoint:source,_=self.checkpoints.load(checkpoint,content_hash(self.exe))
                host=CoopHost(self.exe,folder,('127.0.0.1' if self.local_only else '0.0.0.0',port),campaign_source=source,shared_exploration=shared_exploration)
                if not self.local_only:advertisement=Advertisement(host,name,port)
                with self.lock:self.host=host;self.state='hosting'
                while not self.stop.wait(0.2):
                    if host.failures:raise RuntimeError(host.failures[0])
            else:
                with self.lock:self.state='joining'
                def notice(message):
                    with self.lock:self.notice=message
                previous=[None,0];fps=[None]
                def progress(record):
                    now=time.monotonic();frames=record.get('displayFrames',0)
                    if previous[0] is None:previous[:]=[now,frames]
                    elif now-previous[0]>=1:
                        fps[0]=round((frames-previous[1])/(now-previous[0]),1);previous[:]=[now,frames]
                    with self.lock:self.client_progress={'clientSnapshot':record['seq'],'clientFps':fps[0],'lastReplicaUpdate':now,'campaignMap':record.get('campaignMap',{})}
                result=run_client(self.exe,folder,(address,port),token,stop_event=self.stop,native_campaign=native_campaign,
                                  control_scheme=control_scheme,notice_callback=notice,progress_callback=progress)
                with self.lock:self.result=result
                if result['failures']:raise RuntimeError(result['failures'][0])
        except Exception as error:
            with self.lock:self.error=str(error)
        finally:
            if advertisement:advertisement.close()
            if host:
                try:host.close()
                except Exception as error:
                    with self.lock:self.error=str(error)
            with self.lock:self.host=None;self.state='error' if self.error else 'idle'

    def shutdown(self):
        self.stop.set()
        with self.lock:
            if self.worker and self.worker.is_alive():self.state='stopping'

    def save(self):
        with self.lock:
            if self.state!='hosting' or not self.host:raise ValueError('Host a galaxy before saving')
            if self.save_worker and self.save_worker.is_alive():raise ValueError('A save is already in progress')
            host=self.host;name=self.name;self.save_status='saving'
            def worker():
                try:
                    checkpoint=host.checkpoint(self.checkpoints,name)
                    with self.lock:self.last_checkpoint=checkpoint['id'];self.save_status='saved'
                except Exception as error:
                    with self.lock:self.save_status='Save failed: '+str(error)
            self.save_worker=threading.Thread(target=worker,daemon=True);self.save_worker.start()


def make_handler(launcher,csrf,origin):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,data,content_type='application/json'):
            body=json.dumps(data,allow_nan=False).encode() if content_type=='application/json' else data
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

        def valid_host(self):return self.headers.get('Host')==origin.removeprefix('http://')

        def do_GET(self):
            if not self.valid_host():return self.reply(403,{'error':'Invalid local host'})
            if self.path=='/':
                html=(ROOT/'web'/'native.html').read_text(encoding='utf-8').replace('__SESSION_TOKEN__',csrf).encode()
                return self.reply(200,html,'text/html; charset=utf-8')
            return self.reply(404,{'error':'Not found'})

        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin')!=origin or not hmac.compare_digest(self.headers.get('X-Session-Token',''),csrf):
                return self.reply(403,{'error':'Local session authorization required'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:raise ValueError('Invalid request size')
                request=json.loads(self.rfile.read(length))
                if not isinstance(request,dict):raise ValueError('Invalid request')
                if self.path=='/api/status':return self.reply(200,launcher.status())
                if self.path=='/api/discover':return self.reply(200,{'servers':discover()})
                if self.path=='/api/start':launcher.start(request);return self.reply(202,launcher.status())
                if self.path=='/api/stop':launcher.shutdown();return self.reply(202,launcher.status())
                return self.reply(404,{'error':'Not found'})
            except (ValueError,UnicodeError) as error:return self.reply(400,{'error':str(error)})
            except OSError as error:return self.reply(503,{'error':str(error)})
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,default=Path('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe'))
    parser.add_argument('--port',type=int,default=32918)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Invalid launcher port')
    launcher=Launcher(args.exe);origin='http://127.0.0.1:'+str(args.port)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(launcher,secrets.token_hex(32),origin))
    server.daemon_threads=True
    print('Native multiplayer alpha launcher: '+origin,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        launcher.shutdown()
        if launcher.worker:launcher.worker.join(timeout=20)
        server.server_close()


if __name__=='__main__':main()
