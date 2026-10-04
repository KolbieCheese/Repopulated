import net from 'node:net';
import dgram from 'node:dgram';
import { EventEmitter } from 'node:events';
import { Peer, PROTOCOL, REQUIRED_CAPABILITIES, assert, token, digest, secretEqual, integer,
  validateManifest, validateSettings, manifestHash } from './protocol.js';
import { readJson, atomicJson } from './storage.js';

export const defaults = {
  maxPlayers: 8, aiFactions: 7, factions: [2, 3, 4, 8, 11, 12, 15], seed: 12345,
  friendlyFire: true, pauseWhenEmpty: true, sharedExploration: false, tickRate: 30
};

export class GalaxyServer extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.settings = validateSettings(options.settings ?? defaults);
    this.manifest = validateManifest(options.manifest);
    this.sessionId = token();
    this.engineToken = options.engineToken ?? token();
    this.players = new Map();
    this.peers = new Set();
    this.engine = null;
    this.phase = 'lobby';
    this.checkpoint = null;
    this.nextEmpire = 1;
    this.persistChain = Promise.resolve();
    this.server = net.createServer(socket => this.accept(socket));
  }
  async load() {
    if (!this.options.savePath) return;
    let save;
    try { save = await readJson(this.options.savePath); }
    catch (e) { if (e.code === 'ENOENT') return; throw e; }
    assert(save.schema === 1 && typeof save.sessionId === 'string' && save.sessionId.length === 64,
      'BAD_SAVE');
    assert(manifestHash(save.manifest) === manifestHash(this.manifest), 'SAVE_MOD_MISMATCH');
    this.settings = validateSettings(save.settings);
    assert(Array.isArray(save.players) && save.players.length <= this.settings.maxPlayers, 'BAD_SAVE');
    const players = new Map();
    let largestEmpire = 0;
    const empires = new Set();
    for (const p of save.players) {
      assert(typeof p.id === 'string' && /^[a-f0-9]{64}$/.test(p.tokenHash) &&
        integer(p.empire, 1, 0x7fffffff) && this.settings.factions.includes(p.faction) &&
        typeof p.name === 'string' && p.name.length <= 32 && !players.has(p.id) && !empires.has(p.empire), 'BAD_SAVE');
      players.set(p.id, { ...p, peer: null, ready: false, seq: -1 });
      empires.add(p.empire); largestEmpire = Math.max(largestEmpire, p.empire);
    }
    assert(integer(save.nextEmpire, largestEmpire + 1, 0x7fffffff), 'BAD_SAVE');
    assert(save.checkpoint === null || (save.checkpoint && integer(save.checkpoint.tick, 0, Number.MAX_SAFE_INTEGER) &&
      typeof save.checkpoint.world === 'object' && save.checkpoint.world !== null), 'BAD_SAVE');
    this.players = players;
    this.nextEmpire = save.nextEmpire;
    this.sessionId = save.sessionId;
    this.checkpoint = save.checkpoint;
    this.phase = this.checkpoint ? 'suspended' : 'lobby';
  }
  save() {
    if (!this.options.savePath) return Promise.resolve();
    const data = {
      schema: 1, sessionId: this.sessionId, settings: this.settings, manifest: this.manifest,
      nextEmpire: this.nextEmpire, checkpoint: this.checkpoint,
      players: [...this.players.values()].map(({ id, name, faction, empire, tokenHash }) =>
        ({ id, name, faction, empire, tokenHash }))
    };
    // Serialize writes and capture immutable state now, not at a later queue turn.
    const captured = structuredClone(data);
    const write = this.persistChain.then(() => atomicJson(this.options.savePath, captured));
    this.persistChain = write.catch(() => {});
    return write;
  }
  async listen() {
    await this.load();
    await new Promise((resolve, reject) => {
      this.server.once('error', reject);
      this.server.listen(this.options.port ?? 32913, this.options.bind ?? '0.0.0.0', resolve);
    });
    this.port = this.server.address().port;
    this.heartbeat = setInterval(() => { for (const p of this.peers) p.send({ type: 'ping' }); }, 5000);
    if (this.options.discoveryPort !== false) await this.startDiscovery();
    return this;
  }
  async startDiscovery() {
    this.discovery = dgram.createSocket({ type: 'udp4', reuseAddr: true });
    this.discovery.on('message', (data, remote) => {
      if (data.toString() !== 'REPOPULATED_DISCOVER_V1') return;
      const now = Date.now();
      if (now - (this.lastDiscovery ?? 0) < 50) return;
      this.lastDiscovery = now;
      const listing = { type: 'repopulated-server', protocol: PROTOCOL, port: this.port, ...this.listing() };
      this.discovery.send(JSON.stringify(listing), remote.port, remote.address);
    });
    this.discovery.on('error', e => this.emit('warning', 'LAN discovery: ' + e.message));
    await new Promise(resolve => {
      this.discovery.once('error', resolve);
      this.discovery.bind(this.options.discoveryPort ?? 32915, resolve);
    });
  }
  listing() {
    return {
      name: this.options.name ?? 'Repopulated galaxy', phase: this.phase,
      connectedPlayers: [...this.players.values()].filter(p => p.peer).length,
      reservedPlayers: this.players.size, settings: this.settings,
      manifestHash: manifestHash(this.manifest), engineConnected: !!this.engine,
      gameAdapterIncluded: false
    };
  }
  lobby() {
    return { type: 'lobby', ...this.listing(), players: [...this.players.values()].map(p =>
      ({ id: p.id, name: p.name, faction: p.faction, empire: p.empire, ready: p.ready, connected: !!p.peer })) };
  }
  broadcast(msg) { for (const p of this.players.values()) p.peer?.send(msg); }
  changed() { this.broadcast(this.lobby()); this.emit('change'); }
  accept(socket) {
    if (this.peers.size >= 96) { socket.destroy(); return; }
    const peer = new Peer(socket, msg => this.message(peer, msg), () => this.disconnected(peer));
    this.peers.add(peer);
    peer.helloTimer = setTimeout(() => peer.close(), 5000);
  }
  async message(peer, msg) {
    if (peer.socket.destroyed || this.closing) return;
    if (msg.type === 'pong') return;
    const now = Date.now();
    if (now - (peer.rateWindow ?? 0) >= 1000) { peer.rateWindow = now; peer.rateCount = 0; }
    assert(++peer.rateCount <= 120, 'RATE_LIMIT');
    if (!peer.role) {
      assert(msg.type === 'hello' && msg.protocol === PROTOCOL, 'PROTOCOL_MISMATCH');
      clearTimeout(peer.helloTimer);
      if (msg.role === 'probe') { peer.send({ type: 'listing', ...this.listing() }); peer.ending = true; peer.socket.end(); return; }
      if (msg.role === 'engine') return this.attachEngine(peer, msg);
      assert(msg.role === 'player', 'BAD_ROLE');
      return this.join(peer, msg);
    }
    if (peer.role === 'engine') return this.engineMessage(peer, msg);
    const p = this.players.get(peer.playerId);
    assert(p?.peer === peer, 'NOT_CONNECTED');
    if (msg.type === 'ready') {
      assert(this.phase === 'lobby' || this.phase === 'suspended', 'GAME_IN_PROGRESS');
      assert(typeof msg.ready === 'boolean', 'BAD_MESSAGE');
      p.ready = msg.ready; this.changed(); return;
    }
    if (msg.type === 'chat') {
      assert(typeof msg.text === 'string' && msg.text.trim().length > 0 && msg.text.length <= 500, 'BAD_CHAT');
      assert(now - (peer.lastChat ?? 0) >= 500, 'RATE_LIMIT'); peer.lastChat = now;
      this.broadcast({ type: 'chat', playerId: p.id, name: p.name, text: msg.text }); return;
    }
    if (msg.type === 'input') {
      assert(this.phase === 'running' && this.engine, 'ENGINE_UNAVAILABLE');
      assert(integer(msg.seq, 0, Number.MAX_SAFE_INTEGER) && msg.seq > p.seq, 'STALE_INPUT');
      assert(msg.controls && typeof msg.controls === 'object' && !Array.isArray(msg.controls) &&
        Buffer.byteLength(JSON.stringify(msg.controls)) <= 16384, 'BAD_INPUT');
      p.seq = msg.seq;
      // Ignore client-supplied ownership. The adapter receives the server's empire assignment.
      this.engine.send({ type: 'input', playerId: p.id, empire: p.empire, seq: msg.seq, controls: msg.controls }); return;
    }
    throw Object.assign(new Error('Unknown message'), { code: 'BAD_MESSAGE' });
  }
  async join(peer, msg) {
    assert(manifestHash(msg.manifest) === manifestHash(this.manifest), 'MOD_MISMATCH');
    assert(typeof msg.name === 'string' && msg.name.trim().length > 0 && msg.name.length <= 32, 'BAD_NAME');
    if (this.options.password) assert(secretEqual(msg.password, this.options.password), 'BAD_PASSWORD');
    let p, reconnectToken;
    if (msg.resumeToken !== undefined) {
      assert(typeof msg.resumeToken === 'string' && /^[a-f0-9]{64}$/.test(msg.resumeToken), 'BAD_RESUME_TOKEN');
      const hash = digest(msg.resumeToken);
      p = [...this.players.values()].find(p => secretEqual(p.tokenHash, hash));
      assert(p, 'BAD_RESUME_TOKEN'); assert(!p.peer, 'ALREADY_CONNECTED');
      reconnectToken = msg.resumeToken;
    } else {
      assert(this.phase === 'lobby', 'GAME_IN_PROGRESS');
      assert(this.players.size < this.settings.maxPlayers, 'SERVER_FULL');
      assert(this.settings.factions.includes(msg.faction), 'BAD_FACTION');
      reconnectToken = token();
      p = { id: token().slice(0, 24), name: msg.name.trim(), faction: msg.faction, empire: this.nextEmpire++,
        tokenHash: digest(reconnectToken), ready: false, seq: -1, peer: null };
      this.players.set(p.id, p);
    }
    peer.role = 'player'; peer.playerId = p.id; p.peer = peer; p.seq = -1;
    try { await this.save(); } catch (e) { p.peer = null; throw e; }
    if (peer.socket.destroyed) { p.peer = null; return; }
    peer.send({ type: 'welcome', protocol: PROTOCOL, sessionId: this.sessionId, playerId: p.id,
      empire: p.empire, faction: p.faction, resumeToken: reconnectToken });
    if (this.phase === 'running' && this.checkpoint) peer.send({ type: 'snapshot', ...this.checkpoint });
    this.engine?.send({ type: 'player-connected', player: { id: p.id, empire: p.empire, faction: p.faction } });
    this.updatePause(); this.changed();
  }
  attachEngine(peer, msg) {
    const address = peer.socket.remoteAddress;
    assert(['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(address), 'ENGINE_LOCAL_ONLY');
    assert(secretEqual(msg.token, this.engineToken), 'BAD_ENGINE_TOKEN');
    assert(!this.engine, 'ENGINE_ALREADY_CONNECTED');
    assert(msg.gameHash === this.manifest.gameHash && msg.manifestHash === manifestHash(this.manifest), 'ENGINE_BUILD_MISMATCH');
    assert(Array.isArray(msg.capabilities) && REQUIRED_CAPABILITIES.every(c => msg.capabilities.includes(c)), 'ENGINE_CAPABILITY_MISSING');
    peer.role = 'engine'; this.engine = peer;
    peer.send({ type: 'engine-welcome', sessionId: this.sessionId, settings: this.settings,
      manifest: this.manifest, players: this.lobby().players, checkpoint: this.checkpoint });
    this.changed();
  }
  async engineMessage(peer, msg) {
    assert(this.engine === peer, 'NOT_CONNECTED');
    if (msg.type === 'started') {
      assert(this.phase === 'starting' && msg.sessionId === this.sessionId, 'BAD_ENGINE_STATE');
      assert(integer(msg.tick, 0, Number.MAX_SAFE_INTEGER) && msg.tick >= (this.checkpoint?.tick ?? 0) &&
        msg.world && typeof msg.world === 'object' && !Array.isArray(msg.world), 'BAD_SNAPSHOT');
      clearTimeout(this.startTimer);
      this.checkpoint = { tick: msg.tick, world: msg.world };
      await this.save();
      if (this.engine !== peer || peer.socket.destroyed) { this.phase = 'suspended'; this.changed(); return; }
      this.phase = 'running';
      this.broadcast({ type: 'snapshot', ...this.checkpoint });
      this.changed(); this.updatePause(); return;
    }
    if (msg.type === 'snapshot') {
      assert(this.phase === 'running', 'BAD_ENGINE_STATE');
      assert(integer(msg.tick, 0, Number.MAX_SAFE_INTEGER) && msg.tick > (this.checkpoint?.tick ?? -1), 'STALE_SNAPSHOT');
      assert(msg.world && typeof msg.world === 'object' && !Array.isArray(msg.world), 'BAD_SNAPSHOT');
      this.checkpoint = { tick: msg.tick, world: msg.world };
      this.broadcast({ type: 'snapshot', ...this.checkpoint });
      if (Date.now() - (this.lastSave ?? 0) >= 10000) { await this.save(); this.lastSave = Date.now(); }
      return;
    }
    throw Object.assign(new Error('Unknown engine message'), { code: 'BAD_MESSAGE' });
  }
  updatePause() {
    const empty = ![...this.players.values()].some(p => p.peer);
    this.engine?.send({ type: 'pause', paused: this.phase !== 'running' || (this.settings.pauseWhenEmpty && empty) });
  }
  startGame() {
    assert(this.phase === 'lobby' || this.phase === 'suspended', 'GAME_IN_PROGRESS');
    assert(this.engine, 'ENGINE_UNAVAILABLE', 'Native Reassembly adapter is not implemented. Gameplay cannot start.');
    const connected = [...this.players.values()].filter(p => p.peer);
    assert(connected.length > 0 && connected.every(p => p.ready), 'PLAYERS_NOT_READY');
    this.phase = 'starting';
    this.engine.send({ type: 'start', sessionId: this.sessionId, settings: this.settings,
      players: this.lobby().players, checkpoint: this.checkpoint });
    this.startTimer = setTimeout(() => {
      if (this.phase === 'starting') {
        this.phase = this.checkpoint ? 'suspended' : 'lobby'; this.engine?.close(); this.changed();
      }
    }, 10000);
    this.changed();
  }
  async configure(settings) {
    assert(this.phase === 'lobby' && !this.checkpoint, 'SETTINGS_LOCKED');
    const validated = validateSettings(settings);
    assert(validated.maxPlayers >= this.players.size && [...this.players.values()].every(p => validated.factions.includes(p.faction)), 'SETTINGS_CONFLICT');
    this.settings = validated;
    for (const p of this.players.values()) p.ready = false;
    await this.save();
    this.engine?.send({ type: 'settings', settings: this.settings }); this.changed();
  }
  async kick(id) {
    assert(this.phase === 'lobby' && !this.checkpoint, 'ROSTER_LOCKED');
    const p = this.players.get(id); assert(p, 'PLAYER_NOT_FOUND');
    this.players.delete(id); p.peer?.fail('KICKED'); await this.save(); this.changed();
  }
  disconnected(peer) {
    clearTimeout(peer.helloTimer); this.peers.delete(peer);
    if (this.engine === peer) {
      this.engine = null; clearTimeout(this.startTimer);
      if (this.phase === 'running' || this.phase === 'starting') this.phase = this.checkpoint ? 'suspended' : 'lobby';
    }
    const p = this.players.get(peer.playerId);
    if (p?.peer === peer) {
      p.peer = null; p.ready = false;
      this.engine?.send({ type: 'player-disconnected', playerId: p.id, empire: p.empire });
    }
    this.updatePause(); this.changed();
  }
  async close() {
    this.closing = true;
    clearInterval(this.heartbeat); clearTimeout(this.startTimer);
    for (const p of this.peers) p.close();
    this.discovery?.close();
    await new Promise(resolve => this.server.close(resolve));
    await this.save();
  }
}
