"""Bounded private native host/client control and smoothness tests.

The default accepts physical mouse and keyboard input in Repopulated Live
Client. --test-controls and --smooth-flight use scripted fixtures instead.
Records intent/host statistics once a second for external observation.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import traceback
import native_coop
from native_session import NativeSession


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--port',type=int,default=32927)
    parser.add_argument('--control-scheme',choices=['MOUSE_ROT','KEY_ROT','CARDINAL'],default='MOUSE_ROT')
    parser.add_argument('--test-controls',action='store_true')
    parser.add_argument('--test-menus',action='store_true')
    parser.add_argument('--test-host-menus',action='store_true')
    parser.add_argument('--predict-local',action='store_true',help='Exercise experimental native pilot movement prediction')
    parser.add_argument('--smooth-flight',action='store_true',help='Repeat a native straight/turn/brake fixture for motion comparison')
    parser.add_argument('--comparison-view',action='store_true',help='Test-only matched camera and zoom for the same remote ship in both recordings')
    parser.add_argument('--comparison-frame-limit',action='store_true',help='Test-only host 60 Hz limit to compare native effects at the client frame rate')
    parser.add_argument('--thrust-audit',action='store_true',help='Test-only native emitter diagnostics for the remote pilot')
    parser.add_argument('--scene-idle-gate',action='store_true',help='Enable native replica scene updates between completed draws; omitted for an ungated control fixture')
    parser.add_argument('--presentation-delay-ms',type=int,default=100,help='Source-time interpolation buffer, 0–200 ms (default 100)')
    parser.add_argument('--record',action='store_true',help='Record both private game windows with bounded retained history')
    parser.add_argument('--record-seconds',type=int,default=35)
    args=parser.parse_args()
    if not 15<=args.seconds<=600:parser.error('Test duration must be 15–600 seconds')
    if not 0<=args.presentation_delay_ms<=200:parser.error('Presentation delay must be 0–200 ms')
    if args.comparison_frame_limit and not args.comparison_view:parser.error('Comparison frame limit requires comparison view')
    root=Path(__file__).resolve().parents[1]/'.runtime';stamp=str(time.time_ns())
    report=root/('live-controls-'+stamp+'.json');sessions=[];stop=threading.Event()
    recorder=None
    if args.record:
        from record_native_pair import NativePairRecorder
        recorder=NativePairRecorder(args.record_seconds)
        print('Recording folder: '+str(recorder.folder),flush=True)
    class TrackedSession(NativeSession):
        def __init__(self,exe,folder,config,callback,**kwargs):
            config=dict(config,windowTitle='Reassembly — Repopulated Live Client' if config.get('nativeCampaignClient') else 'Reassembly — Repopulated Live Host',measureRendering=True)
            if args.smooth_flight:config['measureMotion']=True
            if args.comparison_view:config.update(measureMotion=True,testComparisonView={'ident':0x70000002,'zoom':2},testComparisonFrameLimit=args.comparison_frame_limit)
            if args.thrust_audit:config['testThrustAudit']=0x70000002
            if config.get('nativeCampaignClient'):config.update(predictPilot=args.predict_local,testLocalPrediction=args.predict_local and args.test_controls,testSmoothFlight=args.smooth_flight,presentationDelayMs=args.presentation_delay_ms,sceneIdleGate=args.scene_idle_gate,testSceneIdleGate=args.scene_idle_gate)
            elif args.smooth_flight:config['testQuietWorld']=True
            super().__init__(exe,folder,config,callback,**kwargs);sessions.append(self)
    native_coop.NativeSession=TrackedSession
    host=native_coop.CoopHost(args.exe,root/('live-host-'+stamp),('127.0.0.1',args.port),test_menus=args.test_host_menus)
    def monitor():
        while not stop.wait(1):
            clients=[s for s in sessions if s is not host.game]
            messages=list(clients[0].messages) if clients else []
            intents=[m for m in messages if m.get('type')=='native-navigation-intent']
            applied=[m for m in messages if m.get('type')=='world-applied']
            if recorder and not recorder.started and clients and len(applied)>=3:
                try:recorder.start(host.game.pid,clients[0].pid)
                except Exception as error:
                    print('Recording failed: '+str(error),flush=True)
            report.write_text(json.dumps({'hostPid':host.game.pid,'clientPid':clients[0].pid if clients else None,
                'hostScenes':host.scene_count,'hostMotion':host.motion_count,'hostInputs':host.input_count,'networkStage':getattr(clients[0],'network_stage',None) if clients else None,
                'weaponEnabledIntents':sum(any(w[1] for w in m['weapons']) for m in intents),
                'lastIntents':intents[-8:],'nativeWeaponAccepts':host.native_fires,'hostFailures':host.failures,'peerDisconnects':host.peer_disconnects,
                'threadStacks':{str(key):traceback.format_stack(frame)[-6:] for key,frame in sys._current_frames().items()},
                'movementIntents':sum(any(abs(v)>0.01 for v in m['destination'][2:4]) for m in intents),
                'lastApply':applied[-1] if applied else None},indent=2))
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    print('Native controls test report: '+str(report),flush=True)
    try:
        result=native_coop.run_client(args.exe,root/('live-client-'+stamp),('127.0.0.1',args.port),host.token,args.seconds,
            test_controls=args.test_controls and not args.predict_local and not args.smooth_flight,control_scheme=args.control_scheme,test_menus=args.test_menus,notice_callback=lambda text:print(text,flush=True))
        result['scriptedNativePrediction']=args.predict_local and args.test_controls
        result['scriptedSmoothFlight']=args.smooth_flight
        result['quietWorldFixture']=args.smooth_flight
        result['presentationDelayMs']=args.presentation_delay_ms
        result['matchedComparisonView']=args.comparison_view
        result['matchedComparisonFrameLimit']=args.comparison_frame_limit
        result['thrustAudit']=args.thrust_audit
        result['sceneIdleGateRequested']=args.scene_idle_gate
        result['quietWorldStats']=next((m for m in reversed(host.game.messages) if m.get('type')=='quiet-world-fixture'),None)
        result['hostDeliveryTiming']=host.delivery_summary()
        result['hostFrameTiming']=host.frame_timing_snapshot()
        if recorder:result['recordingFolder']=str(recorder.folder)
        if result['displayFrames']<max(30,(args.seconds-5)*10):
            result['failures'].append('Native presentation did not progress throughout the test: '+str(result['displayFrames'])+' frames')
        result.update(hostFailures=host.failures,hostInputs=host.input_count,nativeWeaponAccepts=host.native_fires)
        if recorder:
            recorder.metadata['testOutcome']='failed' if result['failures'] or host.failures else 'passed'
            recorder.metadata['testReport']=str(root/('live-controls-result-'+stamp+'.json'))
            recorder.metadata['testFailures']=result['failures']+list(host.failures)
            recorder.metadata['sceneIdleGateRequested']=args.scene_idle_gate
            recorder.metadata['sceneIdleGate']=result['sceneIdleGate']
        (root/('live-controls-result-'+stamp+'.json')).write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
        if result['failures'] or host.failures:raise RuntimeError('Native control session failed')
    except Exception as error:
        if recorder:
            recorder.metadata['testOutcome']='failed'
            recorder.metadata['testError']=str(error)
        raise
    finally:
        stop.set();thread.join(timeout=2)
        if recorder:recorder.close()
        host.close()


if __name__=='__main__':main()
