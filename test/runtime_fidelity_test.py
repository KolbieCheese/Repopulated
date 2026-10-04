from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from runtime_fidelity import audit_health,health_entities


class RuntimeFidelityTests(unittest.TestCase):
    def test_health_rounding_and_reordered_vectors(self):
        result=audit_health([{'ident':7,'values':[100,53.854]}],[{'ident':7,'values':[53.85,100]}])
        self.assertTrue(result['nativeHealthMultisetsEquivalent'])
        self.assertEqual(result['blocksCompared'],2)

    def test_reset_health_is_detected(self):
        result=audit_health([{'ident':7,'values':[53.85]}],[{'ident':7,'values':[100]}])
        self.assertFalse(result['nativeHealthMultisetsEquivalent'])
        self.assertEqual(len(result['mismatches']),1)

    def test_id_count_and_value_failures_are_rejected(self):
        source=[{'ident':7,'values':[1]}]
        for other in [[{'ident':8,'values':[1]}],[{'ident':7,'values':[1,2]}]]:
            with self.assertRaises(ValueError):
                audit_health(source,other)
        for value in [True,float('nan'),float('inf'),-1,1e10]:
            with self.assertRaises(ValueError):
                health_entities([{'ident':7,'values':[value]}])
        with self.assertRaises(ValueError):
            health_entities(source+source)
