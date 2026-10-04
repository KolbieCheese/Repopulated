"""Loopback-only native research controls; not the multiplayer engine protocol."""
import json
import math
import secrets
import socket
import socketserver
import threading


class ControlBridge:
    def __init__(self, enqueue, factions=(7, 8)):
        self.enqueue = enqueue
        self.tokens = {secrets.token_hex(32): faction for faction in factions}
        self.errors = []
        bridge = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                faction = None
                previous = -1
                for _ in range(32):
                    raw = self.rfile.readline(4097)
                    if not raw:
                        return
                    try:
                        if len(raw) > 4096 or not raw.endswith(b'\n'):
                            raise ValueError('FRAME_LIMIT')
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            raise ValueError('BAD_MESSAGE')
                        if faction is None:
                            if set(message) != {'token'} or message['token'] not in bridge.tokens:
                                raise ValueError('AUTH_FAILED')
                            faction = bridge.tokens[message['token']]
                            answer = {'type': 'authenticated', 'faction': faction}
                        else:
                            fields={'seq', 'targetFaction', 'x', 'y'}
                            if set(message) not in (fields, fields | {'action'}):
                                raise ValueError('BAD_MESSAGE')
                            action=message.get('action','move')
                            if action not in ('move','fire','drive'):
                                raise ValueError('BAD_ACTION')
                            seq = message['seq']
                            if type(seq) is not int or not previous < seq <= 1000000:
                                raise ValueError('STALE_SEQUENCE')
                            previous = seq
                            if type(message['targetFaction']) is not int or message['targetFaction'] != faction:
                                raise ValueError('NOT_OWNER')
                            for axis in ('x', 'y'):
                                value = message[axis]
                                if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1000000:
                                    raise ValueError('BAD_POSITION')
                                if action=='drive' and abs(value)>1:
                                    raise ValueError('BAD_DRIVE')
                            bridge.enqueue({'ownerFaction': faction,
                                            'targetFaction': faction, 'x': message['x'], 'y': message['y'],
                                            'seq': seq, 'action': action})
                            answer = {'type': 'queued', 'seq': seq}
                    except (ValueError, TypeError) as error:
                        answer = {'type': 'error', 'code': str(error)}
                    except Exception as error:
                        bridge.errors.append(str(error))
                        answer = {'type': 'error', 'code': 'BRIDGE_FAILED'}
                    self.wfile.write(json.dumps(answer).encode() + b'\n')
                    self.wfile.flush()
                    if answer.get('code') in ('AUTH_FAILED', 'FRAME_LIMIT'):
                        return

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def exercise(self, include_fire=False):
        results = []
        for token, faction in self.tokens.items():
            with socket.create_connection(self.server.server_address, timeout=3) as sock:
                sock.settimeout(3)
                stream = sock.makefile('rwb')

                def request(message):
                    stream.write(json.dumps(message).encode() + b'\n')
                    stream.flush()
                    return json.loads(stream.readline(4097))

                authenticated = request({'token': token})
                if authenticated.get('faction') != faction:
                    raise RuntimeError('Research authentication failed')
                other = next(value for value in self.tokens.values() if value != faction)
                denied = request({'seq': 0, 'targetFaction': other, 'x': 0, 'y': 0})
                if denied.get('code') != 'NOT_OWNER':
                    raise RuntimeError('Cross-faction network input was accepted')
                command = {'seq': 1, 'targetFaction': faction, 'x': 6000 if faction == next(iter(self.tokens.values())) else 0, 'y': 3000}
                accepted = request(command)
                stale = request(command)
                if accepted.get('type') != 'queued' or stale.get('code') != 'STALE_SEQUENCE':
                    raise RuntimeError('Research queue or replay validation failed')
                result={'faction': faction, 'crossFactionDenied': True, 'replayDenied': True}
                if include_fire:
                    denied=request(dict(command,seq=2,targetFaction=other,action='fire'))
                    firing=dict(command,seq=3,action='fire')
                    accepted=request(firing); stale=request(firing)
                    if denied.get('code')!='NOT_OWNER' or accepted.get('type')!='queued' or stale.get('code')!='STALE_SEQUENCE':
                        raise RuntimeError('Weapon ownership or replay validation failed')
                    result.update(weaponCrossFactionDenied=True,weaponReplayDenied=True)
                results.append(result)
        return results

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
