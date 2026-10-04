from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_coop import client_presentation_delay


class NativePresentationDelayTests(unittest.TestCase):
    def test_native_campaign_default_and_explicit_low_latency_option(self):
        self.assertEqual(client_presentation_delay(True),100)
        self.assertEqual(client_presentation_delay(True,0),0)
        self.assertEqual(client_presentation_delay(True,200),200)

    def test_legacy_sandbox_remains_unbuffered_with_campaign_default_or_override(self):
        self.assertEqual(client_presentation_delay(False),0)
        self.assertEqual(client_presentation_delay(False,200),0)
        for invalid in (-1,201,True,100.0):
            with self.assertRaisesRegex(ValueError,'Invalid presentation delay'):client_presentation_delay(False,invalid)


if __name__=='__main__':unittest.main()
