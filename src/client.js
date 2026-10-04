import net from 'node:net';
import dgram from 'node:dgram';
import { EventEmitter } from 'node:events';
import { Peer, PROTOCOL, assert, integer } from './protocol.js';

export function endpoint(host, port) {
  assert(typeof host === 'string' && host.length > 0 && host.length <= 253 && !/[\s/]/.test(host) &&
    integer(port, 1, 65535), 'BAD_ENDPOINT');
}
export class LobbyClient extends EventEmitter {
  async connect(options) {
    endpoint(options.host, options.port);
    this.disconnect();
    this.lobby = null; this.welcome = null; this.error = null; this.snapshot = null;
    const socket = net.createConnection({ host: options.host, port: options.port });
    const peer = new Peer(socket, msg => {
      if (this.peer !== peer) return;
      if (msg.type === 'ping') { peer.send({ type: 'pong' }); return; }
      if (msg.type === 'welcome') this.welcome = msg;
      if (msg.type === 'lobby') this.lobby = msg;
      if (msg.type === 'error') this.error = msg.code;
      if (msg.type === 'snapshot') this.snapshot = msg;
      this.emit('message', msg);
    }, () => { if (this.peer === peer) this.emit('disconnected'); });
    this.peer = peer;
    socket.once('connect', () => peer.send({ ...options, type: 'hello', protocol: PROTOCOL, role: 'player' }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => finish(new Error('Connection timed out')), 6000);
      const receive = msg => {
        if (msg.type === 'welcome') finish(null, msg);
        if (msg.type === 'error') finish(Object.assign(new Error(msg.code), { code: msg.code }));
      };
      const closed = () => finish(new Error('Connection closed'));
      const finish = (err, value) => {
        clearTimeout(timer); this.off('message', receive); this.off('disconnected', closed);
        if (err) { socket.destroy(); reject(err); } else resolve(value);
      };
      this.on('message', receive); this.on('disconnected', closed);
    });
  }
  send(msg) { assert(this.peer && !this.peer.socket.destroyed && this.welcome, 'NOT_CONNECTED'); this.peer.send(msg); }
  disconnect() { this.peer?.close(); this.peer = null; }
  state() {
    return { connected: !!this.peer && !this.peer.socket.destroyed && !!this.welcome,
      lobby: this.lobby, error: this.error, playerId: this.welcome?.playerId };
  }
}
export async function probe(host, port) {
  endpoint(host, port);
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host, port });
    const timer = setTimeout(() => { socket.destroy(); reject(new Error('Probe timed out')); }, 1500);
    const peer = new Peer(socket, msg => {
      if (msg.type === 'listing') { clearTimeout(timer); socket.end(); resolve({ host, port, ...msg }); }
    }, () => { clearTimeout(timer); reject(new Error('Server unavailable')); });
    socket.once('connect', () => peer.send({ type: 'hello', role: 'probe', protocol: PROTOCOL }));
  });
}
export async function discover(port = 32915, timeout = 800) {
  const socket = dgram.createSocket('udp4');
  const found = new Map();
  return new Promise(resolve => {
    let finished = false;
    const finish = () => { if (finished) return; finished = true; clearTimeout(timer); socket.close(); resolve([...found.values()]); };
    const timer = setTimeout(finish, timeout);
    socket.on('error', finish);
    socket.on('message', (bytes, remote) => {
      if (bytes.length > 8192) return;
      try {
        const msg = JSON.parse(bytes);
        if (msg.type === 'repopulated-server' && msg.protocol === PROTOCOL && integer(msg.port, 1, 65535))
          found.set(`${remote.address}:${msg.port}`, { ...msg, host: remote.address });
      } catch {}
    });
    socket.bind(0, () => {
      socket.setBroadcast(true);
      for (const target of ['255.255.255.255', '127.0.0.1'])
        socket.send('REPOPULATED_DISCOVER_V1', port, target);
    });
  });
}
