import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile, mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { once } from 'node:events';
import { GalaxyServer, defaults } from '../src/server.js';
import { LobbyClient, probe, discover } from '../src/client.js';
import { PROTOCOL, Peer, REQUIRED_CAPABILITIES, manifestHash, secretEqual, MAX_FRAME } from '../src/protocol.js';
import { buildManifest } from '../src/manifest.js';
import { launchWeb } from '../src/web.js';

const manifest = { schema: 1, gameHash: 'a'.repeat(64), mods: [{ id: 'workshop:1', hash: 'b'.repeat(64) }] };
const hello = (server, extra = {}) => ({ host: '127.0.0.1', port: server.port, name: 'Pilot', faction: 8, manifest, ...extra });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function eventually(fn) {
  for (let i = 0; i < 100; i++) { if (fn()) return; await sleep(10); }
  assert.ok(fn(), 'Timed out waiting for state');
}
async function make(t, options = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'repopulated-'));
  const server = new GalaxyServer({ port: 0, bind: '127.0.0.1', discoveryPort: false,
    savePath: path.join(root, 'galaxy.json'), manifest, ...options });
  await server.listen();
  t.after(async () => { if (!server.closing) await server.close(); await rm(root, { recursive: true, force: true }); });
  return { server, root };
}
function client(t) { const c = new LobbyClient(); t.after(() => c.disconnect()); return c; }

// Fixture speaks the contract only. It does not simulate or attach to Reassembly.
async function engine(t, server, extra = {}) {
  const socket = net.createConnection({ host: '127.0.0.1', port: server.port });
  const messages = [];
  const peer = new Peer(socket, msg => { messages.push(msg); if (msg.type === 'ping') peer.send({ type: 'pong' }); });
  t.after(() => peer.close());
  await once(socket, 'connect');
  peer.send({ type: 'hello', protocol: PROTOCOL, role: 'engine', token: server.engineToken,
    gameHash: manifest.gameHash, manifestHash: manifestHash(manifest), capabilities: REQUIRED_CAPABILITIES, ...extra });
  await eventually(() => messages.length > 0);
  return { peer, messages };
}

test('TCP lobby assigns distinct empires, probes status, hashes secrets and reconnects after restart', async t => {
  const { server, root } = await make(t);
  const a = client(t), b = client(t);
  const first = await a.connect(hello(server, { name: 'Alpha' }));
  const second = await b.connect(hello(server, { name: 'Beta' }));
  assert.notEqual(first.empire, second.empire); assert.equal(first.faction, second.faction);
  const listing = await probe('127.0.0.1', server.port);
  assert.equal(listing.connectedPlayers, 2); assert.equal(listing.engineConnected, false);
  await server.save();
  assert.ok(!(await readFile(path.join(root, 'galaxy.json'), 'utf8')).includes(first.resumeToken));
  assert.throws(() => server.startGame(), { code: 'ENGINE_UNAVAILABLE' });
  await server.close();
  const resumed = new GalaxyServer({ port: 0, bind: '127.0.0.1', discoveryPort: false,
    savePath: path.join(root, 'galaxy.json'), manifest });
  await resumed.listen();
  const c = client(t);
  const identity = await c.connect(hello(resumed, { resumeToken: first.resumeToken }));
  assert.equal(identity.playerId, first.playerId); assert.equal(identity.empire, first.empire);
  assert.equal(identity.sessionId, first.sessionId);
  await resumed.close();
});

test('content, faction, capacity, password and reconnect validation fail closed', async t => {
  const { server } = await make(t, { settings: { ...defaults, maxPlayers: 1 }, password: 'crew' });
  await assert.rejects(client(t).connect(hello(server)), { code: 'BAD_PASSWORD' });
  await assert.rejects(client(t).connect(hello(server, { password: 'crew', faction: 999 })), { code: 'BAD_FACTION' });
  const wrong = { ...manifest, mods: [{ id: 'workshop:1', hash: 'c'.repeat(64) }] };
  await assert.rejects(client(t).connect(hello(server, { password: 'crew', manifest: wrong })), { code: 'MOD_MISMATCH' });
  await assert.rejects(client(t).connect(hello(server, { password: 'crew', resumeToken: 'd'.repeat(64) })), { code: 'BAD_RESUME_TOKEN' });
  const c = client(t); const welcome = await c.connect(hello(server, { password: 'crew' }));
  await assert.rejects(client(t).connect(hello(server, { password: 'crew' })), { code: 'SERVER_FULL' });
  await assert.rejects(client(t).connect(hello(server, { password: 'crew', resumeToken: welcome.resumeToken })), { code: 'ALREADY_CONNECTED' });
  assert.equal(server.players.size, 1);
});

test('engine authentication, build and capabilities are enforced', async t => {
  const { server } = await make(t);
  let e = await engine(t, server, { token: 'wrong' }); assert.equal(e.messages[0].code, 'BAD_ENGINE_TOKEN');
  e = await engine(t, server, { capabilities: ['player-input'] }); assert.equal(e.messages[0].code, 'ENGINE_CAPABILITY_MISSING');
  e = await engine(t, server, { gameHash: 'e'.repeat(64) }); assert.equal(e.messages[0].code, 'ENGINE_BUILD_MISMATCH');
  assert.equal(server.engine, null);
});

test('start, checkpoint, ownership, suspend and restore work with a socket protocol fixture', async t => {
  const { server, root } = await make(t);
  const c = client(t); const w = await c.connect(hello(server));
  const e = await engine(t, server);
  assert.throws(() => server.startGame(), { code: 'PLAYERS_NOT_READY' });
  c.send({ type: 'ready', ready: true }); await eventually(() => server.players.get(w.playerId).ready);
  server.startGame(); assert.equal(server.phase, 'starting');
  await eventually(() => e.messages.some(m => m.type === 'start'));
  e.peer.send({ type: 'started', sessionId: server.sessionId, tick: 0, world: { fixture: true, ships: [] } });
  await eventually(() => server.phase === 'running');
  assert.throws(() => server.startGame(), { code: 'GAME_IN_PROGRESS' });
  await assert.rejects(server.configure(defaults), { code: 'SETTINGS_LOCKED' });
  c.send({ type: 'input', seq: 1, empire: 999, playerId: 'intruder', controls: { thrust: 1 } });
  await eventually(() => e.messages.some(m => m.type === 'input'));
  const input = e.messages.find(m => m.type === 'input');
  assert.equal(input.empire, w.empire); assert.equal(input.playerId, w.playerId);
  e.peer.send({ type: 'snapshot', tick: 30, world: { fixture: true, ships: [{ id: 's1' }] } });
  await eventually(() => c.snapshot?.tick === 30);
  await server.save();
  const saved = JSON.parse(await readFile(path.join(root, 'galaxy.json'), 'utf8'));
  assert.equal(saved.checkpoint.tick, 30);
  e.peer.close(); await eventually(() => server.phase === 'suspended');
  const replacement = await engine(t, server); assert.equal(replacement.messages[0].checkpoint.tick, 30);
  server.startGame();
  replacement.peer.send({ type: 'started', sessionId: server.sessionId, tick: 30, world: saved.checkpoint.world });
  await eventually(() => server.phase === 'running');
  c.send({ type: 'input', seq: 1, controls: { thrust: 1 } });
  await eventually(() => c.error === 'STALE_INPUT');
  assert.equal(replacement.messages.filter(m => m.type === 'input').length, 0);
});

test('host settings clear readiness and offline seats remain reserved until removed', async t => {
  const { server } = await make(t);
  assert.equal(server.settings.sharedExploration, false);
  const c = client(t); const w = await c.connect(hello(server));
  c.send({ type: 'ready', ready: true }); await eventually(() => server.players.get(w.playerId).ready);
  await assert.rejects(server.configure({ ...defaults, sharedExploration: 'false' }), { code: 'BAD_SETTINGS' });
  await server.configure({ ...defaults, seed: 42, aiFactions: 4, sharedExploration: true });
  assert.equal(server.settings.sharedExploration, true);
  assert.equal(server.players.get(w.playerId).ready, false);
  await assert.rejects(server.configure({ ...defaults, factions: [2] }), { code: 'SETTINGS_CONFLICT' });
  c.disconnect(); await eventually(() => !server.players.get(w.playerId).peer);
  assert.equal(server.players.size, 1); await server.kick(w.playerId); assert.equal(server.players.size, 0);
});

test('corrupt or incompatible saves never silently create a replacement world', async t => {
  const { server, root } = await make(t); await server.close();
  const file = path.join(root, 'galaxy.json'), bytes = await readFile(file, 'utf8');
  await writeFile(file, '{broken');
  let next = new GalaxyServer({ manifest, savePath: file }); await assert.rejects(next.load(), SyntaxError);
  assert.equal(await readFile(file, 'utf8'), '{broken');
  await writeFile(file, bytes);
  next = new GalaxyServer({ manifest: { ...manifest, gameHash: 'f'.repeat(64) }, savePath: file });
  await assert.rejects(next.load(), { code: 'SAVE_MOD_MISMATCH' });
});

test('mod hashes are reproducible, detect byte changes and preserve order', async t => {
  const { root } = await make(t);
  const exe = path.join(root, 'game.exe'); await writeFile(exe, 'fixture');
  const dir = path.join(root, 'mod'); await mkdir(dir); await writeFile(path.join(dir, 'blocks.lua'), 'a');
  const spec = { gameExe: exe, mods: [{ id: 'local:one', path: dir }] };
  const initial = await buildManifest(spec); assert.deepEqual(initial, await buildManifest(spec));
  await writeFile(path.join(dir, 'blocks.lua'), 'b');
  assert.notEqual(initial.mods[0].hash, (await buildManifest(spec)).mods[0].hash);
  const multi = { ...manifest, mods: [...manifest.mods, { id: 'local:two', hash: 'c'.repeat(64) }] };
  assert.notEqual(manifestHash(multi), manifestHash({ ...multi, mods: [...multi.mods].reverse() }));
});

test('malformed, oversized and wrong-version frames disconnect safely', async t => {
  const { server } = await make(t);
  async function frame(bytes) {
    const socket = net.createConnection({ host: '127.0.0.1', port: server.port });
    const chunks = []; socket.on('data', b => chunks.push(b)); socket.on('error', () => {});
    await once(socket, 'connect'); const closed = new Promise(r => socket.once('close', r));
    socket.write(bytes); await closed; return Buffer.concat(chunks).toString();
  }
  assert.match(await frame('{bad}\n'), /BAD_JSON/);
  assert.match(await frame(JSON.stringify({ type: 'hello', role: 'probe', protocol: 99 }) + '\n'), /PROTOCOL_MISMATCH/);
  await frame(Buffer.alloc(MAX_FRAME + 1, 65)); assert.equal(secretEqual('é', 'aa'), false);
});

test('web UI enforces token and origin and surfaces missing game engine', async t => {
  const { server } = await make(t);
  const web = await launchWeb({ galaxy: server, port: 0, manifest }); t.after(() => web.close());
  const base = `http://127.0.0.1:${web.port}`;
  const html = await (await fetch(base)).text(); assert.match(html, /gameplay adapter missing/);
  const csrf = /name="repopulated-token" content="([a-f0-9]+)"/.exec(html)[1];
  assert.equal((await fetch(base + '/api/state')).status, 400);
  const headers = { 'X-Repopulated-Token': csrf, 'Content-Type': 'application/json' };
  const state = await (await fetch(base + '/api/state', { headers })).json(); assert.equal(state.gameAdapterIncluded, false);
  let r = await fetch(base + '/api/start', { method: 'POST', headers, body: '{}' });
  assert.equal((await r.json()).error, 'BAD_ORIGIN');
  r = await fetch(base + '/api/start', { method: 'POST', headers: { ...headers, Origin: base }, body: '{}' });
  assert.equal((await r.json()).error, 'ENGINE_UNAVAILABLE');
  assert.equal((await fetch(base + '/app.js')).status, 200);
});

test('LAN discovery returns a TCP-verifiable server', async t => {
  const { server } = await make(t, { discoveryPort: 0 });
  const found = await discover(server.discovery.address().port, 300);
  assert.ok(found.some(s => s.port === server.port));
  assert.equal((await probe('127.0.0.1', server.port)).name, 'Repopulated galaxy');
});

test('an authoritative checkpoint survives a full transport restart', async t => {
  const { server, root } = await make(t);
  const c = client(t), w = await c.connect(hello(server));
  const e = await engine(t, server);
  c.send({ type: 'ready', ready: true }); await eventually(() => server.players.get(w.playerId).ready);
  server.startGame();
  e.peer.send({ type: 'started', sessionId: server.sessionId, tick: 120, world: { fixture: true, resourceBalance: 45 } });
  await eventually(() => server.phase === 'running');
  await server.close();
  const restored = new GalaxyServer({ port: 0, bind: '127.0.0.1', discoveryPort: false,
    manifest, savePath: path.join(root, 'galaxy.json') });
  await restored.listen();
  try {
    assert.equal(restored.phase, 'suspended');
    assert.equal(restored.checkpoint.world.resourceBalance, 45);
    await assert.rejects(client(t).connect(hello(restored)), { code: 'GAME_IN_PROGRESS' });
    const r = client(t); const rejoined = await r.connect(hello(restored, { resumeToken: w.resumeToken }));
    assert.equal(rejoined.empire, w.empire);
    const adapter = await engine(t, restored); assert.equal(adapter.messages[0].checkpoint.tick, 120);
    r.send({ type: 'ready', ready: true }); await eventually(() => restored.players.get(w.playerId).ready);
    restored.startGame();
    adapter.peer.send({ type: 'started', sessionId: restored.sessionId, ...restored.checkpoint });
    await eventually(() => restored.phase === 'running');
  } finally { await restored.close(); }
});

test('chat reaches peers as text and empty sessions pause the adapter', async t => {
  const { server } = await make(t);
  const a = client(t), b = client(t);
  const aw = await a.connect(hello(server, { name: 'Alpha' }));
  const bw = await b.connect(hello(server, { name: 'Beta' }));
  const received = [];
  b.on('message', msg => received.push(msg));
  a.send({ type: 'chat', text: '<script>plain text</script>' });
  await eventually(() => received.some(m => m.type === 'chat'));
  assert.equal(received.find(m => m.type === 'chat').name, 'Alpha');
  const e = await engine(t, server);
  a.send({ type: 'ready', ready: true }); b.send({ type: 'ready', ready: true });
  await eventually(() => server.players.get(aw.playerId).ready && server.players.get(bw.playerId).ready);
  server.startGame(); e.peer.send({ type: 'started', sessionId: server.sessionId, tick: 0, world: { fixture: true } });
  await eventually(() => server.phase === 'running');
  a.disconnect(); b.disconnect();
  await eventually(() => e.messages.some(m => m.type === 'pause' && m.paused));
  assert.equal(server.players.size, 2);
});
