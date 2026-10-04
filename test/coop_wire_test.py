from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_wire import snapshot,decode_snapshot,control,PILOT

SCENE=b'cluster{position={12,34},blocks={{800,command={ident=0x70000002,faction=20008}}}}'


class CoopWireTests(unittest.TestCase):
    def test_native_navigation_preserves_rotation_and_server_ownership(self):
        message={'type':'input','seq':1,'action':'native','dimensions':8,'destination':[0,0,0,0,0,-2],
                 'precision':[10,10,0.01,0.01],'weapons':[[123,32,1000,0,0,0,0]]}
        result=control(message,0)
        self.assertEqual(result['destination'][-1],-2);self.assertEqual(result['ownerFaction'],20008)
        for patch in ({'ownerFaction':100},{'dimensions':True},{'dimensions':0x200},
                      {'destination':[0,0,0,0,float('nan'),0]},{'precision':[-1,10,0.01,0.01]},
                      {'weapons':[[123,1,1000,0,0,0,0]]},{'weapons':message['weapons']*2},{'seq':0}):
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):control(dict(message,**patch),0)

    def test_scene_roundtrip_and_pose(self):
        message=snapshot(SCENE,1,1)
        data,pose=decode_snapshot(message,0)
        self.assertEqual(data,SCENE)
        self.assertEqual(pose,[12,34,0,0,0])
        encoded,encoded_pose=snapshot(SCENE,1,1,with_pose=True)
        self.assertEqual(encoded,message);self.assertEqual(encoded_pose,pose)

    def test_reordered_fragment_tag_is_preserved(self):
        data=SCENE+b'cluster{blocks={{803,{0,0}},{803,{10,0},persistentIdent=1234}}}'
        self.assertEqual(decode_snapshot(snapshot(data,1,2),0)[0],data)

    def test_invalid_snapshot_and_modified_payload(self):
        original=snapshot(SCENE,1,1)
        for patch in ({'seq':True},{'seq':0},{'roots':True},{'roots':2},{'payload':'!'},
                      {'sha256':'0'*64},{'extra':'ignored'},{'payload':'A'*2800001}):
            with self.subTest(patch=list(patch)):
                with self.assertRaises(ValueError):decode_snapshot(dict(original,**patch),0)
        with self.assertRaises(ValueError):decode_snapshot(original,1)
        with self.assertRaises(ValueError):snapshot(SCENE.replace(b'20008',b'100'),1,1)
        with self.assertRaises(ValueError):snapshot(SCENE.replace(b'0x70000002',b'0x70000001'),1,1)

    def test_owner_is_assigned_on_server(self):
        result=control({'type':'input','seq':1,'action':'drive','x':-1,'y':1},0)
        self.assertEqual(result['ownerFaction'],20008)
        self.assertEqual(result['targetFaction'],20008)
        with self.assertRaises(ValueError):control(dict(result,type='input'),0)

    def test_invalid_controls_replays_and_nonfinite_numbers(self):
        original={'type':'input','seq':2,'action':'drive','x':1,'y':0}
        for patch in ({'seq':True},{'seq':1},{'action':'build'},{'x':1.01},{'x':True},
                      {'x':float('nan')},{'y':float('inf')},{'ownerFaction':100}):
            with self.subTest(patch=list(patch)):
                with self.assertRaises(ValueError):control(dict(original,**patch),1)
        self.assertEqual(control(dict(original,action='fire',x=10000),1)['x'],10000)

    def test_aim_angle_is_bounded_and_only_for_drive(self):
        message={'type':'input','seq':1,'action':'drive','x':0,'y':1,'angle':1.5}
        self.assertEqual(control(message,0)['angle'],1.5)
        for patch in ({'angle':True},{'angle':float('nan')},{'angle':4},{'action':'fire'}):
            with self.assertRaises(ValueError):control(dict(message,**patch),0)

    def test_fire_button_release_and_wrong_action_rejected(self):
        message={'type':'input','seq':1,'action':'fire','x':100,'y':0,'held':False}
        self.assertFalse(control(message,0)['held'])
        self.assertTrue(control(dict(message,held=True),0)['held'])
        for patch in ({'held':0},{'held':1},{'held':'true'},{'action':'drive'}):
            with self.assertRaises(ValueError):control(dict(message,**patch),0)


if __name__=='__main__':unittest.main()
