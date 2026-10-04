import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from native_coop import scene_gate_enabled


class NativeSceneGateActivationTests(unittest.TestCase):
    def test_actual_native_configuration_is_separate_from_requested_settings(self):
        self.assertFalse(scene_gate_enabled([]))
        self.assertFalse(scene_gate_enabled([{'sceneIdleGate':True}]))
        self.assertFalse(scene_gate_enabled([{'type':'native-scene-gate','action':'failed'}],{'state':0}))
        self.assertTrue(scene_gate_enabled([{'type':'native-scene-gate','action':'configured'}]))
        for state in (1,2,3,4,1.0):self.assertTrue(scene_gate_enabled([],{'state':state}))
        for state in (None,0,5,-1,True,'2',2.5,float('nan')):
            self.assertFalse(scene_gate_enabled([],{'state':state}))

    def test_client_report_uses_actual_event_or_state_even_when_requested_flag_differs(self):
        tree=ast.parse((ROOT/'tools/native_coop.py').read_text(encoding='utf8'))
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='run_client')
        report=next(node for node in ast.walk(function) if isinstance(node,ast.Return) and isinstance(node.value,ast.Dict))
        value=next(value for key,value in zip(report.value.keys,report.value.values) if isinstance(key,ast.Constant) and key.value=='sceneIdleGate')
        code=compile(ast.Expression(value),'actual-client-gate-report','eval')
        context=dict(scene_gate_enabled=scene_gate_enabled,game=SimpleNamespace(messages=[]),updates=[],config={'sceneIdleGate':True})
        self.assertFalse(eval(code,context))
        context['config']['sceneIdleGate']=False;context['game'].messages=[{'type':'native-scene-gate','action':'configured'}]
        self.assertTrue(eval(code,context))
        context['game'].messages=[];context['updates']=[{'sceneGate':{'state':1}}]
        self.assertTrue(eval(code,context))

    def test_real_client_config_enables_only_native_campaign_path(self):
        tree=ast.parse((ROOT/'tools/native_coop.py').read_text(encoding='utf8'))
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='run_client')
        config=next(node for node in ast.walk(function) if isinstance(node,ast.Assign) and
                    isinstance(node.value,ast.Dict) and any(isinstance(target,ast.Name) and target.id=='config' for target in node.targets))
        native=next(node for node in ast.walk(function) if isinstance(node,ast.If) and
                    isinstance(node.test,ast.Name) and node.test.id=='native_campaign' and
                    any(isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Call) and
                        isinstance(statement.value.func,ast.Attribute) and statement.value.func.attr=='update' and
                        isinstance(statement.value.func.value,ast.Name) and statement.value.func.value.id=='config' for statement in node.body))
        code=compile(ast.Module(body=[config,native],type_ignores=[]),'actual-client-configuration','exec')
        for native_campaign in (True,False):
            context=dict(PILOT=0x70000002,test_controls=False,health_validator=None,presentation_delay_ms=100,
                         native_campaign=native_campaign,control_scheme='MOUSE_ROT',test_menus=False,welcome={'sharedExploration':False})
            exec(code,context)
            self.assertIs(context['config'].get('sceneIdleGate',False),native_campaign)
        host=next(node for node in ast.walk(tree) if isinstance(node,ast.Dict) and
                  any(isinstance(key,ast.Constant) and key.value=='campaignRemote' for key in node.keys))
        self.assertNotIn('sceneIdleGate',[key.value for key in host.keys if isinstance(key,ast.Constant)])

    def test_control_harness_explicitly_disables_both_settings_and_preserves_actual_report(self):
        tree=ast.parse((ROOT/'tools/check_native_live_controls.py').read_text(encoding='utf8'))
        tracked=next(node for node in ast.walk(tree) if isinstance(node,ast.ClassDef) and node.name=='TrackedSession')
        class FakeNativeSession:
            def __init__(self,exe,folder,config,callback,**kwargs):self.config=config
        for enabled in (False,True):
            context=dict(NativeSession=FakeNativeSession,sessions=[],args=SimpleNamespace(
                smooth_flight=False,comparison_view=False,comparison_frame_limit=False,thrust_audit=False,
                scene_idle_gate=enabled,predict_local=False,test_controls=False,presentation_delay_ms=100))
            exec(compile(ast.Module(body=[tracked],type_ignores=[]),'actual-control-harness','exec'),context)
            client=context['TrackedSession'](None,None,{'replica':True,'nativeCampaignClient':True,'sceneIdleGate':True},None)
            self.assertIs(client.config['sceneIdleGate'],enabled)
            self.assertIs(client.config['testSceneIdleGate'],enabled)
            host=context['TrackedSession'](None,None,{'campaignRemote':True},None)
            self.assertNotIn('sceneIdleGate',host.config)
        actual_overwrites=[node for node in ast.walk(tree) if isinstance(node,ast.Assign) and
                           any(isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=='result' and
                               isinstance(target.slice,ast.Constant) and target.slice.value=='sceneIdleGate' for target in node.targets)]
        self.assertEqual(actual_overwrites,[])
        coop=ast.parse((ROOT/'tools/native_coop.py').read_text(encoding='utf8'))
        main=next(node for node in coop.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        result_updates=[node for node in ast.walk(main) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and
                        isinstance(node.func.value,ast.Name) and node.func.value.id=='result' and node.func.attr=='update']
        self.assertFalse(any(keyword.arg=='sceneIdleGate' for node in result_updates for keyword in node.keywords))


if __name__=='__main__':unittest.main()
