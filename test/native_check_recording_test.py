from collections import Counter
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_coop import CheckRecording,NativeSession,main


class FakeRecorder:
    def __init__(self,fail=False):
        self.metadata={};self.folder=Path('private-recording');self.started=threading.Event();self.closed=False;self.fail=fail
    def _save(self):pass
    def start(self,host_pid,client_pid):
        if self.fail:raise RuntimeError('Encoder unavailable')
        self.metadata['pids']=[host_pid,client_pid];self.started.set()
    def close(self):self.closed=True;self.metadata['complete']=self.started.is_set()


class NativeCheckRecordingTests(unittest.TestCase):
    def test_capture_starts_after_native_acknowledgments_and_uses_owned_titles(self):
        recorder=FakeRecorder();check=CheckRecording(40,Path('report.json'),recorder=recorder);configs=[]
        def native_init(session,exe,folder,config,*args,**kwargs):
            configs.append(config);session.pid=len(configs);session.message_counts=Counter()
        with patch.object(NativeSession,'__init__',native_init):
            host=check.session_factory(None,None,{'replica':False},None)
            client=check.session_factory(None,None,{'replica':True},None)
        self.assertEqual([cfg['windowTitle'] for cfg in configs],
                         ['Reassembly — Repopulated Live Host','Reassembly — Repopulated Live Client'])
        self.assertTrue(all(cfg['measureRendering'] for cfg in configs))
        self.assertTrue(all(cfg['measureMotion'] for cfg in configs))
        client.message_counts['world-applied']=2;check.begin(host)
        self.assertFalse(recorder.started.wait(0.1))
        client.message_counts['world-applied']=3;self.assertTrue(recorder.started.wait(1))
        check.require_complete()
        self.assertEqual(recorder.metadata['testOutcome'],'in-progress')
        check.finalize(RuntimeError('Health postcondition failed'))
        self.assertEqual(recorder.metadata['testOutcome'],'failed')
        self.assertEqual(recorder.metadata['testError'],'Health postcondition failed')
        self.assertEqual(recorder.metadata['pids'],[host.pid,client.pid])

    def test_failed_or_absent_capture_cannot_pass(self):
        recorder=FakeRecorder();check=CheckRecording(40,Path('report.json'),recorder=recorder)
        with self.assertRaisesRegex(RuntimeError,'both complete videos'):check.require_complete()
        check.finalize()
        self.assertTrue(recorder.closed);self.assertEqual(recorder.metadata['testOutcome'],'failed')

    def test_optional_gate_keeps_positional_recorder_and_only_enables_native_client(self):
        recorder=FakeRecorder();check=CheckRecording(40,Path('report.json'),recorder,scene_idle_gate=True);configs=[]
        def native_init(session,exe,folder,config,*args,**kwargs):configs.append(config)
        with patch.object(NativeSession,'__init__',native_init):
            check.session_factory(None,None,{'replica':False},None)
            check.session_factory(None,None,{'replica':True,'nativeCampaignClient':True},None)
            with self.assertRaisesRegex(ValueError,'native campaign client'):
                check.session_factory(None,None,{'replica':True},None)
        self.assertNotIn('testSceneIdleGate',configs[0])
        self.assertTrue(configs[1]['testSceneIdleGate'])
        self.assertTrue(recorder.metadata['sceneIdleGateRequested'])
        self.assertFalse(recorder.metadata['sceneIdleGate'])
        with self.assertRaisesRegex(ValueError,'boolean fixture'):CheckRecording(40,Path('report.json'),recorder,scene_idle_gate=1)

    def test_recording_reports_configured_gate_evidence_without_equating_request_and_actual(self):
        recorder=FakeRecorder();check=CheckRecording(40,Path('report.json'),recorder)
        def native_init(session,exe,folder,config,*args,**kwargs):
            session.message_history=[];session.lifecycle_history=[]
        with patch.object(NativeSession,'__init__',native_init):
            client=check.session_factory(None,None,{'replica':True,'nativeCampaignClient':True,'sceneIdleGate':True},None)
        self.assertTrue(recorder.metadata['sceneIdleGateRequested'])
        check.close();self.assertFalse(recorder.metadata['sceneIdleGate'])
        client.lifecycle_history=[(1,{'type':'native-scene-gate','action':'configured'})]
        check.close();self.assertTrue(recorder.metadata['sceneIdleGate'])
        # Native state is independent confirmation even when configuration events are unavailable.
        client.lifecycle_history=[];client.message_history=[(2,{'type':'world-applied','sceneGate':{'state':2}})]
        check.close();self.assertTrue(recorder.metadata['sceneIdleGate'])

    def test_gate_cli_rejects_unrecorded_noncheck_or_legacy_sessions_before_launch(self):
        arguments=[['check'],['host','--record'],['join','--record'],['check','--record','--no-native-campaign-client']]
        for args in arguments:
            with self.subTest(args=args),patch.object(sys,'argv',['native_coop',*args,'--exe','unused.exe','--scene-idle-gate']),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:main()
                self.assertEqual(raised.exception.code,2)

    def test_delayed_start_is_scheduled_from_native_readiness_on_monitor_thread(self):
        recorder=FakeRecorder();check=CheckRecording(60,Path('report.json'),recorder,record_delay_seconds=55)
        class Session:
            def __init__(self,pid):self.pid=pid;self.message_counts=Counter()
        host=Session(1);client=Session(2);check.sessions=[host,client]
        ticks=iter([0,20,50,74,75]);clock=[0];polls=[]
        def poll(seconds):
            polls.append(seconds);clock[0]=next(ticks);client.message_counts['world-applied']=2 if clock[0]==0 else 3;return False
        check.stop.wait=poll
        class InlineThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
            def join(self,timeout):pass
            def is_alive(self):return False
        with patch('native_coop.threading.Thread',InlineThread),patch('native_coop.time.monotonic',lambda:clock[0]),patch('native_coop.time.time_ns',lambda:int(clock[0]*1e9)):
            check.begin(host)
        self.assertEqual(polls,[0.05]*5)
        self.assertEqual(recorder.metadata['recordDelaySeconds'],55)
        self.assertEqual(recorder.metadata['nativeReadyAtMs'],20000)
        self.assertEqual(recorder.metadata['recordStartRequestedAtMs'],75000)
        self.assertEqual(recorder.metadata['actualRecordDelaySeconds'],55)
        self.assertEqual(recorder.metadata['pids'],[1,2])
        check.require_complete()

    def test_delay_bounds_and_cli_duration_account_for_the_delayed_capture(self):
        for delay in (-1,3601,True,1.5):
            with self.subTest(delay=delay),self.assertRaisesRegex(ValueError,'whole seconds'):
                CheckRecording(60,Path('report.json'),FakeRecorder(),record_delay_seconds=delay)
        CheckRecording(60,Path('report.json'),FakeRecorder(),record_delay_seconds=3600)
        arguments=[['check','--record-delay-seconds','1'],
                   ['check','--record','--record-delay-seconds','-1'],
                   ['check','--record','--record-delay-seconds','3601'],
                   ['check','--record','--seconds','180','--record-seconds','120','--record-delay-seconds','56']]
        for args in arguments:
            with self.subTest(args=args),patch.object(sys,'argv',['native_coop',*args,'--exe','unused.exe']),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:main()
                self.assertEqual(raised.exception.code,2)


if __name__=='__main__':unittest.main()
