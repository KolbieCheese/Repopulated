from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from replica_plan import ReplicaPlan,HEADER,POSE,HEALTH

SCENE=b'cluster{position={12,34},blocks={{800,persistentIdent=10,command={ident=0x70000002,faction=20008,energy=12,resources=3}},{803,{10,0},persistentIdent=11,health=25,lifetime=10,growFrac=0.5}}}'


class ReplicaPlanTests(unittest.TestCase):
    def test_movement_health_energy_and_timers_preserve_objects(self):
        planner=ReplicaPlan(SCENE)
        changed=SCENE.replace(b'12,34',b'30,40').replace(b'health=25',b'health=20').replace(b'energy=12',b'energy=9').replace(b'resources=3',b'resources=2').replace(b'lifetime=10',b'lifetime=9').replace(b'growFrac=0.5',b'growFrac=0.7')
        plan=planner.prepare(changed)
        self.assertEqual(plan['remove'],[]);self.assertIsNone(plan['additions']);self.assertEqual(plan['retained'],1)
        self.assertEqual(POSE.unpack(bytes.fromhex(plan['poses']))[2:],(30,40,0,0,0,9,2,0))
        states=list(HEALTH.iter_unpack(bytes.fromhex(plan['health'])))
        self.assertEqual(states[0],(10,-1,-1,-1));self.assertEqual(states[1][:2],(11,20))
        self.assertAlmostEqual(states[1][2],0.7,places=6);self.assertEqual(states[1][3],9)

    def test_health_returning_to_default_does_not_replace(self):
        plan=ReplicaPlan(SCENE).prepare(SCENE.replace(b',health=25',b''))
        self.assertIsNone(plan['additions'])
        self.assertEqual(list(HEALTH.iter_unpack(bytes.fromhex(plan['health'])))[1][1],-1)

    def test_structural_changes_and_removals_replace_only_affected_ship(self):
        extra=b'cluster{blocks={{803,persistentIdent=20}}}'
        planner=ReplicaPlan(SCENE+extra)
        changed=SCENE.replace(b'{803,{10,0}',b'{805,{10,0}')
        plan=planner.prepare(changed)
        self.assertEqual(plan['remove'],[20,0x70000002]);self.assertEqual(plan['replaced'],1)
        self.assertNotIn(b'persistentIdent = 20',plan['additions'])
        self.assertEqual(ReplicaPlan(plan['additions']).current.keys(),{0x70000002})

    def test_dropped_or_unacknowledged_plan_does_not_advance_baseline(self):
        planner=ReplicaPlan(HEADER)
        first=planner.prepare(SCENE)
        self.assertEqual(planner.prepare(SCENE)['replaced'],1)
        planner.commit(first)
        self.assertEqual(planner.prepare(SCENE)['retained'],1)

    def test_rejects_unidentified_blocks_duplicate_ids_and_executable_content(self):
        for data in (SCENE.replace(b'persistentIdent=11',b'persistentIdent=10'),SCENE.replace(b'persistentIdent=11,',b''),SCENE+b'os.execute("bad")',SCENE.replace(b'health=25',b'health=-1000000001')):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):ReplicaPlan(HEADER).prepare(data)

    def test_terminal_native_health_is_zero_until_authoritative_removal(self):
        plan=ReplicaPlan(SCENE).prepare(SCENE.replace(b'health=25',b'health=-6.7'))
        self.assertIsNone(plan['additions'])
        self.assertEqual(list(HEALTH.iter_unpack(bytes.fromhex(plan['health'])))[1][1],0)

    def test_unknown_changes_remain_structural(self):
        planner=ReplicaPlan(SCENE)
        plan=planner.prepare(SCENE.replace(b'energy=12',b'energy=12,objective=99'))
        self.assertEqual(plan['replaced'],1)


if __name__=='__main__':unittest.main()
