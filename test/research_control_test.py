import json
from pathlib import Path
import socket
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from research_control import ControlBridge


class ResearchControlTests(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.bridge = ControlBridge(self.commands.append, (10008,20008))

    def tearDown(self):
        self.bridge.close()

    def connect(self):
        sock = socket.create_connection(self.bridge.server.server_address, timeout=2)
        self.addCleanup(sock.close)
        stream = sock.makefile('rwb')
        self.addCleanup(stream.close)
        return stream

    def request(self, stream, message):
        stream.write(json.dumps(message).encode()+b'\n')
        stream.flush()
        return json.loads(stream.readline(4097))

    def test_tcp_ownership_and_replay_use_authenticated_empire(self):
        results = self.bridge.exercise()
        self.assertTrue(all(r['crossFactionDenied'] and r['replayDenied'] for r in results))
        self.assertEqual([c['ownerFaction'] for c in self.commands], [10008,20008])
        self.assertEqual([c['targetFaction'] for c in self.commands], [10008,20008])
        self.assertEqual([c['x'] for c in self.commands], [6000,0])

    def test_bad_auth_spoofed_owner_and_invalid_coordinates_never_enqueue(self):
        bad = self.connect()
        self.assertEqual(self.request(bad, {'token':'invalid'})['code'], 'AUTH_FAILED')
        stream = self.connect()
        token, faction = next(iter(self.bridge.tokens.items()))
        self.request(stream, {'token':token})
        base = {'seq':0,'targetFaction':faction,'x':1,'y':2}
        self.assertEqual(self.request(stream, dict(base,ownerFaction=faction))['code'],'BAD_MESSAGE')
        for seq, value in enumerate([True, float('nan'), float('inf'), 1000001, '100']):
            self.assertEqual(self.request(stream,dict(base,seq=seq,x=value))['code'],'BAD_POSITION')
        self.assertEqual(self.commands, [])

    def test_oversized_frame_closes_before_authentication(self):
        stream = self.connect()
        stream.write(b'x'*4097+b'\n'); stream.flush()
        self.assertEqual(json.loads(stream.readline())['code'],'FRAME_LIMIT')
        self.assertEqual(stream.readline(), b'')
        self.assertEqual(self.commands, [])

    def test_weapon_inputs_share_ownership_and_replay_checks(self):
        results=self.bridge.exercise(include_fire=True)
        self.assertTrue(all(r['weaponCrossFactionDenied'] and r['weaponReplayDenied'] for r in results))
        self.assertEqual([c['action'] for c in self.commands], ['move','fire','move','fire'])
        self.assertEqual([c['ownerFaction'] for c in self.commands], [10008,10008,20008,20008])
        stream=self.connect()
        token,faction=next(iter(self.bridge.tokens.items()))
        self.request(stream,{'token':token})
        self.assertEqual(self.request(stream,{'seq':0,'targetFaction':faction,'x':0,'y':0,'action':'spawn'})['code'],'BAD_ACTION')


if __name__ == '__main__':
    unittest.main()
