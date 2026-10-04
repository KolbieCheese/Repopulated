"""Bounded game-window recordings for the private native smoothness harness.

Capture uses the installed FFmpeg CLI, with exact test-window titles. It never
records the desktop or launches another game. Cleanup only touches files owned
by this recorder, retaining the latest test and the latest successful comparison.
"""
import json
from pathlib import Path
import subprocess
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
OWNER='repopulated-record-native-pair-v1'
FILES={'host.mp4','client.mp4','host.log','client.log','recording.json',
       'client-analysis.json','client-analysis.png','client-analysis.txt','client-analysis-relative.png','client-analysis-detail.png',
       'host-analysis.json','host-analysis.png','host-analysis.txt','host-analysis-relative.png',
       'client-analysis-stop-start-detail.png','client-analysis-native-presentation-detail.png',
       'client-analysis-pixel-relative-relative.png','client-analysis-pixel-relative.json','client-analysis-review.txt',
       'client-analysis-thrust.json','client-analysis-thrust-native-sim.json','client-analysis-thrust-mover-window.json','client-analysis-native-acceptance.json',
       'client-analysis-background-detail.png','client-analysis-background.json',
       'client-analysis-native-early-comparison.json','client-analysis-native-late-comparison.json',
       'client-analysis-native-late-presentation-detail.png',
       'native-motion-analysis.json','native-motion-analysis.txt','pair.mp4'}


class NativePairRecorder:
    def __init__(self,seconds=35):
        if type(seconds) is not int or not 5<=seconds<=120:
            raise ValueError('Recording must be 5–120 seconds')
        self.seconds=seconds
        self.root=(ROOT/'.runtime'/'smoothness-recordings').resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.folder=self.root/str(time.time_ns())
        self.folder.mkdir()
        self.processes=[]
        self.logs=[]
        self.started=False
        self.finished=False
        self.lock=threading.Lock()
        self.metadata={'createdBy':OWNER,'secondsRequested':seconds,'capture':'exact game windows',
            'encoding':'H264, scaled to 960 pixels wide, at most two encoder threads per window',
            'captureCaveat':'Capture can drop frames; recording FPS is separate from game render FPS.'}
        self._save()
        self.prune()

    def _save(self):
        (self.folder/'recording.json').write_text(json.dumps(self.metadata,indent=2),encoding='utf-8')

    def prune(self):
        owned=[]
        passed=[]
        finished=set()
        for candidate in self.root.iterdir():
            if not candidate.name.isdigit() or candidate.is_symlink() or not candidate.is_dir():continue
            manifest=candidate/'recording.json'
            try:
                metadata=json.loads(manifest.read_text())
                if manifest.is_symlink() or metadata.get('createdBy')!=OWNER:continue
            except (OSError,ValueError):continue
            owned.append(candidate)
            if metadata.get('testOutcome')=='passed':passed.append(candidate)
            if metadata.get('finishedAt'):finished.add(candidate)
        ordered=sorted(owned,key=lambda p:int(p.name),reverse=True)
        # A failed retry must not evict the last successful comparison video.
        keep=set(ordered[:1])
        if passed:keep.add(max(passed,key=lambda p:int(p.name)))
        for candidate in ordered:
            if len(keep)>=2:break
            keep.add(candidate)
        for candidate in ordered:
            if candidate in keep or candidate not in finished:continue
            if candidate.resolve().parent!=self.root:raise ValueError('Recording cleanup path escaped its folder')
            entries=list(candidate.iterdir())
            if any(p.name not in FILES or p.is_symlink() or not p.is_file() for p in entries):continue
            for path in entries:
                if path.resolve().parent!=candidate.resolve():raise ValueError('Unexpected recording path')
                path.unlink()
            candidate.rmdir()

    def start(self,host_pid,client_pid):
        with self.lock:
            if self.started:return
            ffmpeg=ROOT/'.runtime'/'media-tools'/'imageio_ffmpeg'/'binaries'/'ffmpeg-win-x86_64-v7.1.exe'
            if not ffmpeg.is_file():raise RuntimeError('Existing FFmpeg runtime is unavailable')
            self.started=True
            self.metadata.update(hostPid=host_pid,clientPid=client_pid,startedAt=time.time())
            self.metadata['windowCaptureStarts']={}
            startup=subprocess.STARTUPINFO()
            startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow=0
            try:
                for side in ('host','client'):
                    title='Reassembly — Repopulated Live '+side.title()
                    log=(self.folder/(side+'.log')).open('wb')
                    self.logs.append(log)
                    command=[str(ffmpeg),'-hide_banner','-loglevel','warning',
                        '-f','gdigrab','-framerate','60','-draw_mouse','0','-i','title='+title,
                        '-t',str(self.seconds),'-vf','scale=960:-2','-c:v','libx264',
                        '-preset','ultrafast','-crf','23','-threads','2','-pix_fmt','yuv420p',
                        '-y',str(self.folder/(side+'.mp4'))]
                    spawn_time=time.time()
                    process=subprocess.Popen(command,stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,stderr=log,startupinfo=startup)
                    self.processes.append(process)
                    self.metadata['windowCaptureStarts'][side]={'spawnRequestedAt':spawn_time,
                        'spawnReturnedAt':time.time(),'encoderPid':process.pid}
            except Exception:
                self.close()
                raise
            self._save()

    def close(self):
        if self.finished:return
        self.finished=True
        results=[]
        for process in self.processes:
            if process.poll() is None:
                try:process.communicate(b'q\n',timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.communicate(timeout=5)
            results.append(process.returncode)
        for log in self.logs:log.close()
        self.metadata.update(finishedAt=time.time(),exitCodes=results,
            complete=self.started and len(results)==2 and all(code==0 for code in results)
                and all((self.folder/(side+'.mp4')).stat().st_size>1000
                    for side in ('host','client') if (self.folder/(side+'.mp4')).is_file())
                and all((self.folder/(side+'.mp4')).is_file() for side in ('host','client')))
        self._save()
        # The newest capture's outcome is now known. Re-evaluate retention
        # here so a completed successful run doesn't leave three videos
        # waiting for the next test to start.
        self.prune()

