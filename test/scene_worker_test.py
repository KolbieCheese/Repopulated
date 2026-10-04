from pathlib import Path
import base64
import hashlib
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from coop_wire import snapshot
from scene_worker import SceneWorker


SCENE=b'cluster{position={12,34},blocks={{800,persistentIdent=10,command={ident=0x70000002,faction=20008,energy=12,resources=3}},{803,{10,0},persistentIdent=11,health=25}}}'
VISUALS={'version':2,'blocks':[],'projectiles':[],'thrust':[],'droppedThrust':0,'motion':[[0x70000002,0]]}


class SceneWorkerTests(unittest.TestCase):
    def test_host_worker_matches_validated_wire_snapshot(self):
        worker=SceneWorker('host')
        try:
            prepared=worker.request('snapshot',data=SCENE,seq=1,roots=1,visuals=VISUALS,map=None)
            self.assertEqual(prepared['scene'],snapshot(SCENE,1,1,visuals=VISUALS))
            self.assertEqual(prepared['pose'][:2],[12,34])
            self.assertGreaterEqual(prepared['scenePreparationMs'],0)
        finally:worker.close()

    def test_native_acknowledgement_controls_process_planner_baseline(self):
        worker=SceneWorker('client')
        try:
            message=snapshot(SCENE,1,1,visuals=VISUALS)
            first=worker.request('prepare',scene=message,diagnostics=True)
            self.assertEqual(first['plan']['replaced'],1)
            self.assertEqual(first['plan']['continuity'],[])
            self.assertEqual(first['plan']['transitions'][0]['afterCommand'],[10,20008])
            self.assertNotIn('state',first['plan'])
            with self.assertRaisesRegex(ValueError,'acknowledged'):worker.request('prepare',scene=snapshot(SCENE,2,1,visuals=VISUALS))
            with self.assertRaisesRegex(ValueError,'differs'):worker.request('commit',seq=2)
            worker.request('commit',seq=1)
            changed=SCENE.replace(b'12,34',b'50,60').replace(b'health=25',b'health=20')
            second=worker.request('prepare',scene=snapshot(changed,2,1,visuals=VISUALS))
            self.assertEqual(second['pose'][:2],[50,60]);self.assertEqual(second['plan']['retained'],1)
            self.assertIsNone(second['plan']['additions'])
        finally:worker.close()
        self.assertFalse(worker.process.is_alive())

    def test_worker_rejects_scene_hash_and_ownership_before_native_apply(self):
        worker=SceneWorker('client')
        try:
            message=snapshot(SCENE,1,1,visuals=VISUALS)
            with self.assertRaisesRegex(ValueError,'hash'):worker.request('prepare',scene=dict(message,sha256='0'*64))
            with self.assertRaisesRegex(ValueError,'roots'):worker.request('prepare',scene=dict(message,roots=2))
            wrong_owner=SCENE.replace(b'faction=20008',b'faction=8')
            bad=dict(message,payload=base64.b64encode(wrong_owner).decode(),sha256=hashlib.sha256(wrong_owner).hexdigest())
            with self.assertRaisesRegex(ValueError,'ownership'):worker.request('prepare',scene=bad)
        finally:worker.close()


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    unittest.main()
