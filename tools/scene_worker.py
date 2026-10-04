"""Own-process scene parsing so Lua decoding cannot starve realtime delivery.

The child accepts only data from its parent over a private pipe. Client replica
baselines remain in the child and advance only after native acknowledgement.
"""
import multiprocessing
from pathlib import Path
import sys
import time


def _serve(connection,mode,previous):
    from coop_wire import snapshot,decode_snapshot
    from replica_plan import ReplicaPlan,HEADER
    planner=ReplicaPlan(HEADER) if mode=='client' else None
    pending=None
    connection.send({'ok':True})
    try:
        while True:
            request=connection.recv()
            if request is None:return
            try:
                action=request['action']
                if mode=='host' and action=='snapshot':
                    started=time.perf_counter()
                    scene,pose=snapshot(request['data'],request['seq'],request['roots'],with_pose=True,
                                        visuals=request['visuals'],campaign_map=request['map'])
                    result={'scene':scene,'pose':pose,'scenePreparationMs':(time.perf_counter()-started)*1000}
                elif mode=='client' and action=='prepare':
                    if pending is not None:raise ValueError('Previous native scene has not been acknowledged')
                    scene=request['scene'];started=time.perf_counter()
                    data,pose,parsed=decode_snapshot(scene,previous,planner.read_with_shapes)
                    decoded=(time.perf_counter()-started)*1000;started=time.perf_counter()
                    plan=planner.prepare(data,parsed,diagnostics=request.get('diagnostics',False));pending=(scene['seq'],plan);previous=scene['seq']
                    result={'plan':{key:value for key,value in plan.items() if key!='state'},'pose':pose,
                            'hasBeams':any(row[8]>0 for row in scene['visuals']['blocks']),
                            'sceneDecodeMs':decoded,'scenePlanMs':(time.perf_counter()-started)*1000}
                elif mode=='client' and action=='commit':
                    if pending is None or pending[0]!=request['seq']:raise ValueError('Native scene acknowledgement differs')
                    planner.commit(pending[1]);pending=None;result={}
                else:raise ValueError('Invalid scene worker request')
                connection.send({'ok':True,'value':result})
            except Exception as error:
                connection.send({'ok':False,'error':str(error)})
    except (EOFError,BrokenPipeError):pass
    finally:connection.close()


class SceneWorker:
    def __init__(self,mode,previous=0):
        if mode not in ('host','client'):raise ValueError('Unknown scene worker mode')
        # Source runs use pythonw on Windows. Packaged launchers are already
        # windowed executables; freeze_support handles their worker entry.
        if sys.platform=='win32' and not getattr(sys,'frozen',False):
            windowed=Path(sys.executable).with_name('pythonw.exe')
            if windowed.is_file():multiprocessing.set_executable(str(windowed))
        context=multiprocessing.get_context('spawn')
        self.connection,child=context.Pipe()
        self.process=context.Process(target=_serve,args=(child,mode,previous),name='Repopulated scene '+mode,daemon=True)
        self.process.start();child.close();self.closed=False
        try:self._reply()
        except Exception:self.close();raise

    def _reply(self):
        if not self.connection.poll(20):raise RuntimeError('Scene worker timed out')
        try:reply=self.connection.recv()
        except EOFError:raise RuntimeError('Scene worker exited before replying') from None
        if not reply['ok']:raise ValueError(reply['error'])
        return reply.get('value')

    def request(self,action,**values):
        if self.closed:raise RuntimeError('Scene worker is closed')
        self.connection.send(dict(values,action=action))
        return self._reply()

    def close(self):
        if getattr(self,'closed',True):return
        self.closed=True
        try:self.connection.send(None)
        except (EOFError,BrokenPipeError,OSError):pass
        self.process.join(timeout=2)
        if self.process.is_alive():self.process.terminate();self.process.join(timeout=2)
        self.connection.close()
