import copy
from pathlib import Path
import struct
import tempfile
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from campaign_map_wire import validate_map,pack_map,map_summary,visible_map,load_map_settings


class CampaignMapTest(unittest.TestCase):
    def fixture(self):
        return {'version':1,'exploration':'shared','width':2,'radius':[6000,6000],
                'cells':[[200,.5,0,0],[200,.5,1,1],[201,1,1,0],[0,0,0,0]],
                'regions':[[200,0xffbb00,8],[201,0x00aabb,20008]],'objectives':[[1,40,100,513,100,200,0,0,10]]}

    def test_native_layout_and_success_counts(self):
        data=self.fixture();packed=pack_map(data)
        self.assertEqual(struct.unpack('<ifBBBB',bytes.fromhex(packed['cells'])[:12]),(200,.5,0,0,0,0))
        self.assertEqual(len(bytes.fromhex(packed['cells'])),48)
        self.assertEqual(map_summary(data)['explorationPercent'],50)
        self.assertEqual(map_summary(data)['visibleRegionCellsByFaction'],{'8':1,'20008':1})
        self.assertEqual(map_summary(data)['stations'],1)
        self.assertEqual(len(bytes.fromhex(packed['objectives'])),36)

    def test_rejects_duplicate_or_invalid_objectives(self):
        for index,value in [(0,0),(1,-1),(2,True),(3,0x100000000),(4,float('nan')),(8,-1)]:
            data=self.fixture();data['objectives'][0][index]=value
            with self.assertRaises(ValueError):validate_map(data)
        data=self.fixture();data['objectives'].append(data['objectives'][0])
        with self.assertRaises(ValueError):validate_map(data)

    def test_rejects_ambiguous_or_unresolved_regions(self):
        for change in ('duplicate','missing','extra'):
            data=self.fixture()
            if change=='duplicate':data['regions'].append(data['regions'][0])
            if change=='missing':data['regions'].pop()
            if change=='extra':data['regions'].append([202,0,1])
            with self.assertRaises(ValueError):validate_map(data)

    def test_rejects_unsafe_dimensions_cells_and_flags(self):
        cases=[]
        for key,value in [('width',True),('width',257),('radius',[float('nan'),1]),('exploration','private'),('version',True)]:
            data=self.fixture();data[key]=value;cases.append(data)
        for index,value in [(0,-1),(1,float('inf')),(1,1.1),(2,True),(2,2),(3,-1)]:
            data=self.fixture();data['cells'][0][index]=value;cases.append(data)
        for data in cases:
            with self.assertRaises(ValueError):validate_map(data)

    def test_private_discovery_filters_markers_and_preserves_terrain(self):
        data=self.fixture();data['exploration']='independent'
        # Native toroidal cell 0 is unknown; x=6000 belongs to discovered cell 1.
        data['objectives']=[[1,40,100,513,100,200,0,0,10],[2,41,8,513,6000,200,0,0,10],
                            [3,42,8,513,-6000,200,0,0,10]]
        visible=visible_map(data)
        self.assertEqual([row[0] for row in visible['objectives']],[2,3])
        self.assertEqual(visible['cells'],data['cells'])
        self.assertEqual(visible['regions'],data['regions'])
        self.assertEqual(len(data['objectives']),3)
        self.assertEqual(map_summary(visible)['exploration'],'independent')
        data['exploration']='shared';self.assertEqual(len(visible_map(data)['objectives']),3)

    def test_shared_setting_defaults_off_and_saved_value_is_strict(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            slot=Path(directory);path=slot/'map-settings.json'
            self.assertFalse(load_map_settings(slot)['sharedExploration'])
            path.write_text(json.dumps({'version':1,'sharedExploration':True}))
            self.assertTrue(load_map_settings(slot)['sharedExploration'])
            for value in (1,'false',None):
                path.write_text(json.dumps({'version':1,'sharedExploration':value}))
                with self.assertRaises(ValueError):load_map_settings(slot)

if __name__=='__main__':unittest.main()
