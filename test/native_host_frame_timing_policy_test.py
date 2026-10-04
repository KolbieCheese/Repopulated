from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_coop import CoopHost,frame_timing,host_frame_timing_snapshot


class NativeHostFrameTimingPolicyTests(unittest.TestCase):
    def test_full_native_histogram_is_bounded_owned_and_drives_percentile(self):
        histogram=[0]*251;histogram[17]=99;histogram[18]=1
        source={'histogram':histogram,'longFrames':0,'maxFrameMs':18,'unrelated':'ignored'}
        host=CoopHost.__new__(CoopHost);host.host_frame_timing=host_frame_timing_snapshot(source)
        source['histogram'][17]=0
        saved=host.frame_timing_snapshot()
        self.assertEqual(set(saved),{'histogram','longFrames','maxFrameMs'})
        self.assertEqual(len(saved['histogram']),251)
        self.assertEqual(frame_timing(saved),{'sampledFrames':100,'p95Ms':17,'framesOver50Ms':0,'maxMs':18})
        saved['histogram'][17]=1
        self.assertEqual(host.frame_timing_snapshot()['histogram'][17],99)
        host.host_frame_timing={};self.assertEqual(host.frame_timing_snapshot(),{})

    def test_missing_unbounded_or_malformed_native_histogram_is_rejected(self):
        valid={'histogram':[0]*251,'longFrames':0,'maxFrameMs':0}
        for histogram in (None,{},[],[0]*250,[0]*252,[-1]+[0]*250,[True]+[0]*250):
            with self.subTest(histogram=histogram),self.assertRaisesRegex(ValueError,'histogram'):
                host_frame_timing_snapshot(dict(valid,histogram=histogram))
        for field,value in (('longFrames',True),('longFrames',-1),('maxFrameMs',float('nan')),('maxFrameMs',-1)):
            with self.subTest(field=field,value=value),self.assertRaisesRegex(ValueError,'frame timing'):
                host_frame_timing_snapshot(dict(valid,**{field:value}))


if __name__=='__main__':unittest.main()
