from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from scene_codec import Reader,compare_geometry,geometry

SCENE=b'''offset={0,0}
cluster{position={10,20},velocity={3,4},angle=0.5,blocks={
 {800,{1,2},0.2,command={ident=0x70000000,faction=8,flags=NONE,blueprint={blocks={{800}}}}},
 {803,{5,6},0.3,features=THRUSTER|TORQUER,health=8}}}
'''


class SceneCodecTests(unittest.TestCase):
    def test_nested_blueprints_enums_and_persistent_ids(self):
        scene=geometry(SCENE)
        self.assertEqual(list(scene),[0x70000000])
        self.assertEqual(scene[0x70000000]['blocks'],[[800,1,2,0.2],[803,5,6,0.3]])
        self.assertEqual(compare_geometry(SCENE,SCENE),1)

    def test_geometry_rounding_is_tolerated_but_wrong_blocks_and_ids_fail(self):
        self.assertEqual(compare_geometry(SCENE,SCENE.replace(b'position={10,20}',b'position={10.001,20.001}')),1)
        for other in [SCENE.replace(b'{803,',b'{804,'),SCENE.replace(b'0x70000000',b'0x70000001'),
                      SCENE.replace(b'{5,6}',b'{5,7}'),SCENE.replace(b'position={10,20}',b'position={9000,20}')]:
            with self.assertRaises(ValueError):
                compare_geometry(SCENE,other)

    def test_transient_attributes_are_deliberately_outside_geometry_claim(self):
        changed=SCENE.replace(b'health=8',b'health=1')
        self.assertNotEqual(SCENE,changed)
        self.assertEqual(compare_geometry(SCENE,changed),1)

    def test_executable_syntax_duplicate_fields_and_deep_nesting_are_rejected(self):
        for source in [b'cluster{a=os.execute("bad")}',b'cluster{a=1,a=2}',b'cluster{a='+b'{'*40+b'}'*40+b'}']:
            with self.assertRaises(ValueError):
                Reader(source).scene()

    def test_duplicate_command_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            geometry(SCENE+SCENE)

    def test_block_order_does_not_change_geometry(self):
        other=SCENE.replace(b'{800,{1,2}',b'{800,{1,2}')
        # Swap the two block table entries without changing their contents.
        command=b'{800,{1,2},0.2,command={ident=0x70000000,faction=8,flags=NONE,blueprint={blocks={{800}}}}}'
        thruster=b'{803,{5,6},0.3,features=THRUSTER|TORQUER,health=8}'
        other=other.replace(command+b',\n '+thruster,thruster+b',\n '+command)
        self.assertNotEqual(other,SCENE)
        self.assertEqual(compare_geometry(SCENE,other),1)

    def test_debris_uses_first_block_persistent_id(self):
        debris=b'cluster{blocks={{803,persistentIdent=0x70000002}}}'
        self.assertEqual(list(geometry(debris)),[0x70000002])

    def test_empty_scene_can_represent_all_entities_removed(self):
        empty=b'offset={0,0}\nradius={9000,9000}\n'
        self.assertEqual(geometry(empty),{})
        self.assertEqual(compare_geometry(empty,empty),0)


if __name__=='__main__':
    unittest.main()
