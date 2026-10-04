from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from scene_fidelity import audit_fidelity

SCENE = b'cluster{blocks={{800,command={ident=7,faction=8,flags=NONE}},{803,{1,2},health=8}}}'


class FidelityTests(unittest.TestCase):
    def test_partial_damage_and_missing_defaults_are_reported(self):
        result = audit_fidelity(SCENE, SCENE.replace(b'health=8', b'health=1'))
        self.assertFalse(result['serializedStateEquivalent'])
        self.assertEqual(result['differenceCount'], 1)
        self.assertTrue(result['differences'][0]['path'].endswith('.health'))
        missing = audit_fidelity(SCENE, SCENE.replace(b',health=8', b''))
        self.assertEqual(missing['differences'][0]['replica'], {'present': False})

    def test_ai_flags_lifetime_and_capacity_are_not_geometry_only(self):
        changed = SCENE.replace(b'flags=NONE', b'flags=SOCIAL|WANDER').replace(
            b'health=8', b'health=8,lifetime=3,capacity=100')
        result = audit_fidelity(SCENE, changed)
        self.assertEqual(result['differenceCount'], 3)

    def test_reordering_and_print_precision_are_tolerated(self):
        command = b'{800,command={ident=7,faction=8,flags=NONE}}'
        block = b'{803,{1,2},health=8}'
        changed = SCENE.replace(command + b',' + block, block + b',' + command)
        self.assertTrue(audit_fidelity(SCENE, changed)['serializedStateEquivalent'])
        self.assertTrue(audit_fidelity(SCENE, SCENE.replace(b'health=8', b'health=8.0001'))['serializedStateEquivalent'])

    def test_report_is_bounded_without_hiding_failure(self):
        changed = SCENE.replace(b'flags=NONE', b'flags=WANDER').replace(b'health=8', b'health=1')
        result = audit_fidelity(SCENE, changed, limit=1)
        self.assertEqual(result['differenceCount'], 2)
        self.assertEqual(len(result['differences']), 1)
        self.assertTrue(result['truncated'])
        with self.assertRaises(ValueError):
            audit_fidelity(SCENE, changed, limit=0)

    def test_missing_entity_and_nested_blueprint_changes_are_preserved(self):
        result = audit_fidelity(SCENE, b'offset={0,0}')
        self.assertEqual(result['differenceCount'], 1)
        nested = SCENE.replace(b'flags=NONE', b'flags=NONE,blueprint={blocks={{800,health=5}}}')
        result = audit_fidelity(nested, nested.replace(b'health=5', b'health=2'))
        self.assertEqual(result['differenceCount'], 1)
        self.assertIn('blueprint.blocks', result['differences'][0]['path'])


if __name__ == '__main__':
    unittest.main()
