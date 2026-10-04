from collections import Counter,deque
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_session import NativeSession


def isolated_history(measure_motion=True):
    session=object.__new__(NativeSession)
    session.message_counts=Counter();session.message_index=0
    session.message_history=deque(maxlen=10000);session.lifecycle_history=deque(maxlen=1000)
    session.diagnostic_history=deque(maxlen=30000) if measure_motion else None
    session.alive=False
    return session


class NativeMessageRetentionTests(unittest.TestCase):
    def test_native_menu_damage_and_failure_proof_survive_high_volume_tail_rollover(self):
        session=isolated_history()
        lifecycle=[{'type':'native-menu-test','action':'opened','tab':1},
                   {'type':'fixture-partial-damage-result','result':1},
                   {'type':'motion-apply-failed','seq':12,'poses':'exact rejected poses'}]
        for record in lifecycle:session._remember_message(record)
        motion={'type':'native-motion','seq':4,'inputSeq':2,'sourceTimeMs':1000,'simTimeMs':900,
                'poses':'00'*44,'blocks':'00'*56,'projectiles':'00'*36,'movers':'00'*16,'health':'00'*16,
                'hostFrameTiming':{'histogram':[1],'longFrames':0,'maxFrameMs':20}}
        original=dict(motion);session._remember_message(motion)
        session._remember_message({'type':'presentation-trace','frame':4,'rows':[[100,4,1.5]]})
        session._remember_message({'type':'thrust-audit','frame':4,'rows':[[1,0x70000002,20]],'stats':{'total':1}})
        session._remember_message({'type':'particle-render-audit','frame':4,'row':[1,2,3]})
        for index in range(12000):session._remember_message({'type':'native-navigation-intent','seq':index})
        session._remember_message({'type':'native-menu-test','action':'closed','tab':1})
        messages=session.messages
        self.assertEqual(messages[:3],lifecycle)
        self.assertEqual([(record['action'],record['tab']) for record in messages if record['type']=='native-menu-test'],[('opened',1),('closed',1)])
        self.assertEqual(len([record for record in messages if record['type']=='native-menu-test']),2)
        self.assertEqual(session.message_counts['native-navigation-intent'],12000)
        self.assertEqual(motion,original) # Consumers still receive the complete original record.
        with tempfile.TemporaryDirectory(prefix='repopulated-history-') as temporary:
            session.folder=Path(temporary).resolve();session.close()
            saved=json.loads((session.folder/'instrumentation.json').read_text())
        compact=next(record for record in saved if record['type']=='native-motion')
        self.assertNotIn('poses',compact);self.assertNotIn('histogram',compact['hostFrameTiming'])
        self.assertEqual(compact['counts'],dict(poses=1,blocks=1,projectiles=1,movers=1,health=1))
        self.assertTrue(any(record['type']=='presentation-trace' and record['frame']==4 for record in saved))
        self.assertTrue(any(record['type']=='thrust-audit' and record['frame']==4 for record in saved))
        self.assertTrue(any(record['type']=='particle-render-audit' and record['frame']==4 for record in saved))
        self.assertFalse(any(record['type']=='thrust-audit' for index,record in session.lifecycle_history))
        self.assertFalse(any(record['type']=='particle-render-audit' for index,record in session.lifecycle_history))
        self.assertEqual(saved[:3],lifecycle)

    def test_archives_remain_bounded_and_merge_in_original_order_without_duplicates(self):
        session=isolated_history()
        for index in range(1200):session._remember_message({'type':'host-menu-test','event':index})
        for index in range(30005):session._remember_message({'type':'presentation-trace','frame':index})
        self.assertEqual(len(session.message_history),10000)
        self.assertEqual(len(session.lifecycle_history),1000)
        self.assertEqual(len(session.diagnostic_history),30000)
        messages=session.messages
        self.assertEqual(len(messages),11000)
        self.assertEqual(messages[0],{'type':'host-menu-test','event':200})
        self.assertEqual(messages[-1],{'type':'presentation-trace','frame':30004})
        self.assertEqual([record['event'] for record in messages[:1000]],list(range(200,1200)))
        session._remember_message({'type':'host-menu-test','event':1200})
        self.assertEqual(sum(record=={'type':'host-menu-test','event':1200} for record in session.messages),1)


if __name__=='__main__':unittest.main()
