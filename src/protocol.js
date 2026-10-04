import { randomBytes, createHash, timingSafeEqual } from 'node:crypto';

export const PROTOCOL = 1;
export const MAX_FRAME = 2 * 1024 * 1024;
export const REQUIRED_CAPABILITIES = ['authoritative-world', 'player-input', 'multi-faction', 'checkpoint-restore'];
export const token = () => randomBytes(32).toString('hex');
export const digest = value => createHash('sha256').update(value).digest('hex');
export function secretEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
export function assert(condition, code, message = code) {
  if (!condition) throw Object.assign(new Error(message), { code });
}
export function integer(n, min, max) {
  return Number.isSafeInteger(n) && n >= min && n <= max;
}
export function validateSettings(s) {
  assert(s && typeof s === 'object' && !Array.isArray(s), 'BAD_SETTINGS');
  const keys = ['maxPlayers', 'aiFactions', 'factions', 'seed', 'friendlyFire', 'pauseWhenEmpty', 'sharedExploration', 'tickRate'];
  assert(Object.keys(s).every(k => keys.includes(k)), 'BAD_SETTINGS', 'Unknown setting');
  assert(integer(s.maxPlayers, 1, 32) && integer(s.aiFactions, 0, 64), 'BAD_SETTINGS');
  assert(integer(s.seed, 0, 0xffffffff) && integer(s.tickRate, 10, 60), 'BAD_SETTINGS');
  assert(typeof s.friendlyFire === 'boolean' && typeof s.pauseWhenEmpty === 'boolean', 'BAD_SETTINGS');
  assert(s.sharedExploration === undefined || typeof s.sharedExploration === 'boolean', 'BAD_SETTINGS');
  assert(Array.isArray(s.factions) && s.factions.length > 0 && s.factions.length <= 128 &&
    s.factions.every(f => integer(f, 1, 0x7fffffff)) && new Set(s.factions).size === s.factions.length, 'BAD_SETTINGS');
  return { ...structuredClone(s), sharedExploration: s.sharedExploration ?? false };
}
export function validateManifest(m) {
  assert(m && m.schema === 1 && /^[a-f0-9]{64}$/.test(m.gameHash), 'BAD_MANIFEST');
  assert(Array.isArray(m.mods) && m.mods.length <= 512, 'BAD_MANIFEST');
  const ids = new Set();
  for (const mod of m.mods) {
    assert(mod && typeof mod.id === 'string' && mod.id.length > 0 && mod.id.length <= 128 &&
      /^[a-f0-9]{64}$/.test(mod.hash) && !ids.has(mod.id), 'BAD_MANIFEST');
    ids.add(mod.id);
  }
  // Order is significant: Reassembly relocates identifiers and merges overrides.
  return { schema: 1, gameHash: m.gameHash, mods: m.mods.map(({ id, hash }) => ({ id, hash })) };
}
export function manifestHash(m) { return digest(JSON.stringify(validateManifest(m))); }

// Bounded newline framing with queued writes. Slow peers cannot hold the simulation hostage.
export class Peer {
  constructor(socket, onMessage, onClose = () => {}) {
    this.socket = socket;
    this.pending = Buffer.alloc(0);
    this.chain = Promise.resolve();
    socket.setNoDelay(true);
    socket.setTimeout(15000, () => this.close());
    socket.on('error', () => {});
    socket.on('close', onClose);
    socket.on('data', data => {
      this.pending = Buffer.concat([this.pending, data]);
      let index;
      while ((index = this.pending.indexOf(10)) !== -1) {
        if (index > MAX_FRAME) return this.close();
        const line = this.pending.subarray(0, index);
        this.pending = this.pending.subarray(index + 1);
        let msg;
        try { msg = JSON.parse(line.toString('utf8')); }
        catch { return this.fail('BAD_JSON'); }
        if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return this.fail('BAD_MESSAGE');
        this.queued = (this.queued ?? 0) + 1;
        if (this.queued > 128) return this.fail('RATE_LIMIT');
        this.chain = this.chain.then(() => { if (!this.ending && !socket.destroyed) return onMessage(msg); }).catch(e => this.fail(e.code ?? 'INTERNAL_ERROR'))
          .finally(() => this.queued--);
      }
      if (this.pending.length > MAX_FRAME) this.close();
    });
  }
  send(msg) {
    const data = JSON.stringify(msg) + '\n';
    if (Buffer.byteLength(data) > MAX_FRAME || this.socket.writableLength > MAX_FRAME * 2) {
      this.close(); return false;
    }
    if (this.socket.destroyed) return false;
    this.socket.write(data); return true;
  }
  fail(code) { if (this.ending) return; this.ending = true; this.send({ type: 'error', code }); this.socket.end(); }
  close() { this.socket.destroy(); }
}
