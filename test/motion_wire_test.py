from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from motion_wire import KINEMATICS,MOVER,HEALTH,BLOCK,PROJECTILE,validate_motion


def frame(row,seq=1,ack=-1):
    return {'type':'motion','seq':seq,'sourceTimeMs':seq*50,'simTimeMs':seq*17,'inputSeq':ack,'inputTick':0,'poses':KINEMATICS.pack(*row).hex(),'blocks':'','projectiles':'','movers':'','health':''}


class MotionTests(unittest.TestCase):
    def test_motion_and_acknowledgement_roundtrip(self):
        row=(0x70000002,20008,12,34,50,-60,1,90,20,100,-2)
        value=frame(row,2,14)
        self.assertEqual(validate_motion(value,1),[row])
        for patch in ({'seq':1},{'seq':True},{'sourceTimeMs':True},{'sourceTimeMs':0},{'sourceTimeMs':1e15},{'simTimeMs':True},{'simTimeMs':-1},{'inputSeq':False},{'inputSeq':-2},{'inputTick':True},{'poses':value['poses']*2},{'poses':'00'},{'poses':'x'*88}):
            with self.assertRaises(ValueError):validate_motion(dict(value,**patch),1)

    def test_source_time_is_monotonic_independently_of_sequence(self):
        value=frame((1,8,0,0,0,0,0,0,0,0,0),seq=4)
        self.assertEqual(len(validate_motion(value,3,150)),1)
        for source_time in (150,149):
            with self.assertRaises(ValueError):validate_motion(dict(value,sourceTimeMs=source_time),3,150)

    def test_simulation_clock_can_pause_without_reversing(self):
        value=frame((1,8,0,0,0,0,0,0,0,0,0),seq=4)
        self.assertEqual(len(validate_motion(value,3,150,68)),1)
        with self.assertRaises(ValueError):validate_motion(value,3,150,69)

    def test_rejects_nonfinite_unbounded_and_negative_resources(self):
        row=[1,8,0,0,0,0,0,0,0,0,0]
        for index,value in ((0,0),(2,1e7),(4,10001),(6,float('nan')),(7,-1),(10,float('inf'))):
            changed=row.copy();changed[index]=value
            with self.assertRaises(ValueError):validate_motion(frame(changed))

    def test_effect_drivers_and_damage_share_motion_envelope(self):
        value=frame((1,8,0,0,0,0,0,0,0,0,0))
        value.update(blocks=BLOCK.pack(1,50,832,0,0,0,1,0,0,0,0,0,0,0).hex(),
                     projectiles=PROJECTILE.pack(0,0,100,0,0,2,1,0xffffffff,20).hex(),
                     movers=MOVER.pack(1,60,1,-0.5).hex(),health=HEALTH.pack(50,12,1,-1).hex())
        self.assertEqual(len(validate_motion(value)),1)
        for patch in ({'health':value['health']*2},{'movers':value['movers']*2},
                      {'movers':MOVER.pack(1,60,2,0).hex()},
                      {'health':HEALTH.pack(50,float('nan'),1,-1).hex()},
                      {'blocks':BLOCK.pack(2,50,832,0,0,0,1,0,0,0,0,0,0,0).hex()},
                      {'projectiles':PROJECTILE.pack(0,0,100,0,0,2,-1,0,20).hex()},
                      {'particles':'not part of the protocol'}):
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):validate_motion(dict(value,**patch))

if __name__=='__main__':unittest.main()
