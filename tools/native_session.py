"""Own-process lifecycle for the experimental native cooperative prototype."""
import hashlib
import json
import os
from pathlib import Path
import time
import threading
import shutil
import sys
from collections import Counter,deque
from native_paths import STATE_ROOT,NATIVE_DLL,FROZEN
from run_native_probe import frida,SCRIPT,KNOWN_HASH,create_campaign_fixture


SPAWN_LOCK=threading.Lock()
BULK_MESSAGE_TYPES=frozenset(('native-stage','presentation-trace','thrust-audit','particle-render-audit','ownership-audit','native-navigation-intent',
    'native-motion','world-exported','world-applied','network-drive-result','network-fire-result',
    'native-progress','actor-control-ai-stats','sample-result','native-player-update'))


class NativeSession:
    def __init__(self,exe,folder,config,callback,campaign=False,bootstrap=None,campaign_source=None):
        self.exe=exe.resolve();self.folder=folder.resolve();self.message_counts=Counter();self.pid=None;self.closing=False;self.alive=True
        if hashlib.sha256(self.exe.read_bytes()).hexdigest()!=KNOWN_HASH:
            raise ValueError('Unsupported executable build')
        self.folder.mkdir(parents=True,exist_ok=False)
        root=Path(__file__).resolve().parents[1]
        dll=NATIVE_DLL
        if not dll.is_file():raise ValueError('Build the native diagnostic DLL first')
        cfg=dict(config,sandbox=str(self.folder),dll=str(dll))
        # Long recorded checks keep their compact timing evidence even when the
        # ordinary message tail turns over. Both histories remain bounded.
        self.diagnostic_history=deque(maxlen=30000) if cfg.get('measureMotion') else None
        self.message_history=deque(maxlen=10000)
        self.lifecycle_history=deque(maxlen=1000)
        self.message_index=0
        cfg.setdefault('windowTitle','Reassembly — Repopulated Client' if cfg.get('replica') else 'Reassembly — Repopulated Host')
        env=dict(os.environ,USERPROFILE=str(self.folder),APPDATA=str(self.folder),LOCALAPPDATA=str(self.folder),
                 REPOPULATED_DIAGNOSTIC_LOG=str(self.folder/'native-telemetry.jsonl'))
        env.pop('REPOPULATED_TEST_CONTROL',None)
        argv=[str(self.exe),'kNetworkEnable=0','kPauseOnLostFocus=0','kMaximizeWindow=0','kWindowSize={1280,720}']
        if campaign:
            if campaign_source is None:create_campaign_fixture(self.folder,self.exe.parent.parent/'data'/'ships'/'8_interceptor.lua',cfg.get('controlScheme','MOUSE_ROT'),cfg.get('beamFixture',False),0x70000002 if cfg.get('nativeCampaignClient') else 0x70000001)
            else:
                source=campaign_source.resolve();private_root=STATE_ROOT.resolve()
                if private_root not in source.parents or source.is_symlink():raise ValueError('Resume research requires an isolated private save')
                files=list(source.rglob('*'))
                if len(files)>4096 or any(p.is_symlink() for p in files) or sum(p.stat().st_size for p in files if p.is_file())>256*1024*1024:
                    raise ValueError('Private save size or link limit')
                shutil.copytree(source,self.folder/'Reassembly'/'data'/'save0')
            argv.append('kLoadSlot=0');cfg['trackPlayer']=True
        else:
            if bootstrap is None:raise ValueError('Replica bootstrap required')
            level=self.folder/'bootstrap.lua';level.write_bytes(bootstrap)
            if any(c in level.as_posix() for c in ';\r\n'):raise ValueError('Invalid sandbox path')
            argv.extend(['kHeadlessMode=0','kSandboxScript='+json.dumps(f'level_load {level.as_posix()}; activate; echo finished')])
        self.device=frida.get_local_device()
        SPAWN_LOCK.acquire()
        old=Path.cwd()
        try:
            if FROZEN:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            os.chdir(self.exe.parent)
            self.pid=self.device.spawn(argv,env=env,stdio='pipe')
            self.session=self.device.attach(self.pid)
            def detached(reason,*details):
                if str(reason)=='process-terminated':self.alive=False
                if not self.closing:callback({'type':'error','description':'Game session ended: '+str(reason)})
            self.session.on('detached',detached)
            self.script=self.session.create_script(SCRIPT.replace('__CONFIG__',json.dumps(cfg)),runtime='v8')
            def message(raw,data):
                record=raw.get('payload',raw)
                self._remember_message(record)
                callback(record)
            self.script.on('message',message);self.script.load()
        except Exception:
            if self.pid:self.device.kill(self.pid)
            raise
        finally:
            os.chdir(old)
            SPAWN_LOCK.release()

    def _remember_message(self,record):
        self.message_counts[record.get('type')]+=1
        stored=record
        if record.get('type')=='native-motion':
            stored={key:record[key] for key in ('type','seq','inputSeq','inputTick','sourceTimeMs','simTimeMs','deliveryTiming') if key in record}
            stored['callbackAtMs']=time.time()*1000
            if 'hostFrameTiming' in record:
                stored['hostFrameTiming']={key:record['hostFrameTiming'][key] for key in ('longFrames','maxFrameMs') if key in record['hostFrameTiming']}
            stored['counts']={key:len(record[key])//(size*2) for key,size in (('poses',44),('blocks',56),('projectiles',36),('movers',16),('health',16))}
        self.message_index+=1
        self.message_history.append((self.message_index,stored))
        if record.get('type') not in BULK_MESSAGE_TYPES:self.lifecycle_history.append((self.message_index,stored))
        if self.diagnostic_history is not None and record.get('type') in ('presentation-trace','native-motion','thrust-audit','particle-render-audit','ownership-audit'):
            self.diagnostic_history.append((self.message_index,stored))

    @property
    def messages(self):
        history=dict(self.message_history)
        history.update(self.lifecycle_history)
        return [history[index] for index in sorted(history)]

    def resume(self):self.device.resume(self.pid)

    def close(self):
        self.closing=True
        if self.alive:
            try:self.device.kill(self.pid)
            except frida.ProcessNotFoundError:pass
        history=dict(self.message_history)
        history.update(self.lifecycle_history)
        if self.diagnostic_history is not None:history.update(self.diagnostic_history)
        (self.folder/'instrumentation.json').write_text(json.dumps([history[index] for index in sorted(history)],indent=2))
        log=self.folder/'Reassembly'/'data'/'log_latest.txt'
        for attempt in range(30):
            try:
                text=log.read_text(errors='replace') if log.exists() else ''
                break
            except PermissionError:
                if attempt==29:raise
                time.sleep(0.1)
        if 'Code: EXCEPTION_' in text or '[SDL] Closing log: Crashed' in text:
            raise RuntimeError('Native session crashed; see '+str(log))
