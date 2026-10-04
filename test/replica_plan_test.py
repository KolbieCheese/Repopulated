from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from replica_plan import ReplicaPlan,HEADER,POSE,HEALTH,commandless_generation

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
        self.assertEqual(plan['continuity'],[[0x70000002,10,20008]])
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

    def test_faction_change_replaces_root_before_committing_new_ownership(self):
        planner=ReplicaPlan(SCENE)
        changed=SCENE.replace(b'faction=20008',b'faction=8')
        plan=planner.prepare(changed)
        self.assertEqual(plan['remove'],[0x70000002])
        self.assertEqual(plan['retained'],0)
        self.assertEqual(plan['replaced'],1)
        self.assertEqual(plan['continuity'],[])
        self.assertEqual(POSE.unpack(bytes.fromhex(plan['poses']))[:2],(0x70000002,8))
        self.assertEqual(ReplicaPlan(plan['additions']).current[0x70000002][0]['fields']['blocks']['items'][0]['fields']['command']['fields']['faction'],8)
        # An unacknowledged ownership change must still be replaced on retry.
        self.assertEqual(planner.prepare(changed)['replaced'],1)
        planner.commit(plan)
        self.assertEqual(planner.prepare(changed)['retained'],1)

    def test_commandless_fragment_has_neutral_serialized_faction(self):
        fragment=b'cluster{position={3552,2293},blocks={{803,persistentIdent=0x7000005e},{804,persistentIdent=0x7000005d}}}'
        plan=ReplicaPlan(HEADER).prepare(fragment)
        self.assertEqual(POSE.unpack(bytes.fromhex(plan['poses']))[:2],(0x7000005d,0))
        self.assertEqual(set(ReplicaPlan(plan['additions']).current),{0x7000005d})
        # Block vector order cannot change the fragment's stable root identity.
        reordered=fragment.replace(b'{803,persistentIdent=0x7000005e},{804,persistentIdent=0x7000005d}',b'{804,persistentIdent=0x7000005d},{803,persistentIdent=0x7000005e}')
        planner=ReplicaPlan(fragment)
        self.assertEqual(planner.prepare(reordered)['retained'],1)

    def test_shape_replacement_keeps_only_the_same_command_generation(self):
        planner=ReplicaPlan(SCENE)
        changed=SCENE.replace(b'{803,{10,0}',b'{805,{10,0}')
        plan=planner.prepare(changed,diagnostics=True)
        self.assertEqual(plan['continuity'],[[0x70000002,10,20008]])
        transition=plan['transitions'][0]
        self.assertTrue(transition['sameLifetime'])
        self.assertEqual(transition['changedBlocks'],1)
        self.assertEqual(transition['commandFields'],[])
        # A respawn may reuse the actor's ID but has a different command block.
        respawn=changed.replace(b'persistentIdent=10',b'persistentIdent=30')
        fresh=planner.prepare(respawn,diagnostics=True)
        self.assertEqual(fresh['continuity'],[])
        self.assertFalse(fresh['transitions'][0]['sameLifetime'])
        self.assertEqual(fresh['transitions'][0]['beforeCommand'],[10,20008])
        self.assertEqual(fresh['transitions'][0]['afterCommand'],[30,20008])
        self.assertNotIn('transitions',planner.prepare(changed))

    def test_same_generation_command_change_is_diagnostic_and_commit_is_acknowledged(self):
        planner=ReplicaPlan(SCENE)
        changed=SCENE.replace(b'energy=12',b'energy=12,objective=99')
        plan=planner.prepare(changed,diagnostics=True)
        self.assertEqual(plan['continuity'],[[0x70000002,10,20008]])
        self.assertEqual(plan['transitions'][0]['commandFields'],['objective'])
        planner.commit(plan)
        self.assertEqual(planner.prepare(changed,diagnostics=True)['transitions'],[])

    def test_attached_missile_runtime_does_not_replace_parent(self):
        attached=SCENE[:-1]+b',subclusters={{position={10,0},blocks={{832,persistentIdent=12,lifetime=10,growFrac=1,health=20,command={energy=8,resources=5}}}}}}'
        changed=attached.replace(b'lifetime=10',b'lifetime=9').replace(b'health=20',b'health=18')
        plan=ReplicaPlan(attached).prepare(changed)
        self.assertIsNone(plan['additions']);self.assertEqual(plan['remove'],[])
        self.assertEqual(list(HEALTH.iter_unpack(bytes.fromhex(plan['health'])))[-1],(12,18,1,9))
        # Child commands are not part of the root pose packet yet.
        self.assertEqual(ReplicaPlan(attached).prepare(attached.replace(b'energy=8',b'energy=7'))['replaced'],1)

    def test_commandless_subcluster_pose_change_keeps_only_anchored_parent_history(self):
        original=b'cluster{position={20,30},blocks={{203,persistentIdent=20},{203,{10,0},persistentIdent=21}},subclusters={{position={10,0},angle=0.1,blocks={{203,persistentIdent=22}}}}}'
        changed=original.replace(b'angle=0.1',b'angle=0.2')
        planner=ReplicaPlan(original);plan=planner.prepare(changed,diagnostics=True)
        self.assertEqual(plan['remove'],[20]);self.assertEqual(plan['replaced'],1)
        self.assertEqual(plan['continuity'],[[20,20,0,2]])
        self.assertEqual(POSE.unpack(bytes.fromhex(plan['poses']))[:2],(20,0))
        self.assertIn(b'angle = 0.2',plan['additions'])
        self.assertTrue(plan['transitions'][0]['sameLifetime'])
        self.assertEqual(plan['transitions'][0]['beforeFragment'],[20,0,2])
        self.assertEqual(plan['transitions'][0]['rootFields'],['subclusters'])
        self.assertEqual(planner.prepare(changed)['continuity'],[[20,20,0,2]])
        planner.commit(plan);self.assertEqual(planner.prepare(changed)['continuity'],[])

    def test_commandless_shape_changes_preserve_anchor_but_anchor_loss_or_command_does_not(self):
        original=b'cluster{blocks={{203,persistentIdent=20},{203,persistentIdent=21}}}'
        changed=original.replace(b'203,persistentIdent=21',b'204,persistentIdent=21')
        self.assertEqual(ReplicaPlan(original).prepare(changed)['continuity'],[[20,20,0,2]])
        for new_scene in (original.replace(b'{203,persistentIdent=20},',b''),
                          original.replace(b'persistentIdent=20',b'persistentIdent=19'),
                          original.replace(b'persistentIdent=20',b'persistentIdent=20,command={ident=20,faction=0}'),
                          original.replace(b'persistentIdent=20',b'persistentIdent=20,command={}')):
            with self.subTest(new_scene=new_scene):
                self.assertEqual(ReplicaPlan(original).prepare(new_scene)['continuity'],[])
        owned=original.replace(b'persistentIdent=20',b'persistentIdent=20,command={ident=20,faction=0}')
        self.assertEqual(ReplicaPlan(owned).prepare(original)['continuity'],[])
        self.assertEqual(ReplicaPlan(original).prepare(HEADER)['continuity'],[])

    def test_commandless_anchor_requires_all_unique_positive_valid_top_block_ids(self):
        from copy import deepcopy
        original=b'cluster{blocks={{203,persistentIdent=20},{203,persistentIdent=21}}}'
        entry=ReplicaPlan(original).current[20]
        self.assertIsNone(commandless_generation(entry,21))
        for bid in (0,-1,20,21.5,0x100000000,True,float('nan')):
            invalid=deepcopy(entry);invalid[0]['fields']['blocks']['items'][1]['fields']['persistentIdent']=bid
            self.assertIsNone(commandless_generation(invalid,20))


if __name__=='__main__':unittest.main()
