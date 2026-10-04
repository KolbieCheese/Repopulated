import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from presentation_wire import validate_visuals,pack_visuals,BLOCK,PROJECTILE,THRUST,MOTION

SHIPS={123:{'blocks':[[832,1,2,0]],'blockIds':[999]}}
VISUALS={'version':1,'blocks':[[123,999,832,1,2,0,3,0.7,0.5,10,20,30,40,1]],
         'projectiles':[[10,20,100,0,0,2,1,0xff00ff00,20]],
         'thrust':[[10,20,-100,0,1,0xff00ff00,-80,0,2,0xff0000ff,0.1]],'droppedThrust':0}


class PresentationTests(unittest.TestCase):
    def test_exact_abi_layout_and_roundtrip(self):
        packed=pack_visuals(validate_visuals(VISUALS,SHIPS))
        for key,layout,length in (('blocks',BLOCK,56),('projectiles',PROJECTILE,36),('thrust',THRUST,44)):
            data=bytes.fromhex(packed[key]);self.assertEqual(len(data),length)
            for actual,expected in zip(layout.unpack(data),VISUALS[key][0]):self.assertAlmostEqual(actual,expected,places=5)

    def test_absent_duplicate_or_mismatched_block_rejected(self):
        for index,value in ((0,999),(1,998),(2,998),(3,1.1),(6,4),(13,2)):
            broken=copy.deepcopy(VISUALS);broken['blocks'][0][index]=value
            with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)
        broken=copy.deepcopy(VISUALS);broken['blocks']*=2
        with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)

    def test_particle_budget_time_color_and_finiteness(self):
        for key,index,value in (('thrust',10,0.301),('thrust',5,-1),('projectiles',6,121),
                                ('projectiles',0,float('nan')),('blocks',8,True),('blocks',8,2)):
            broken=copy.deepcopy(VISUALS);broken[key][0][index]=value
            with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)
        broken=copy.deepcopy(VISUALS);broken['thrust']*=513
        with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)

    def test_angular_motion_covers_each_authoritative_entity_once(self):
        value=dict(copy.deepcopy(VISUALS),version=2,motion=[[123,-1.25]])
        packed=pack_visuals(validate_visuals(value,SHIPS))
        self.assertEqual(MOTION.unpack(bytes.fromhex(packed['motion'])),(123,-1.25))
        for rows in ([],[[123,1],[123,2]],[[999,1]],[[123,float('nan')]],[[123,10001]]):
            broken=dict(value,motion=rows)
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)
        broken=copy.deepcopy(VISUALS);broken['thrust'].append(list(broken['thrust'][0]));broken['thrust'][1][-1]=0
        with self.assertRaises(ValueError):validate_visuals(broken,SHIPS)


if __name__=='__main__':unittest.main()
