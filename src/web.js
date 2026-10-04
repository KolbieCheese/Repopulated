import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { LobbyClient, probe, discover, endpoint } from './client.js';
import { token, secretEqual, assert, validateManifest } from './protocol.js';
import { atomicJson, readJson } from './storage.js';

// This controller is loopback-only. A hostile website cannot perform authenticated mutations.
export async function launchWeb({ galaxy = null, port = 32914, manifest = null, profilePath = null }) {
  const csrf = token();
  const client = new LobbyClient();
  const entries = new Map();
  const chats = [];
  let lastRefresh = 0, refreshing = false;
  async function refreshListings() {
    if (refreshing || Date.now() - lastRefresh < 5000) return;
    refreshing = true; lastRefresh = Date.now();
    try {
      const candidates = [...entries.values()];
      for (let i = 0; i < candidates.length; i += 8) {
        await Promise.all(candidates.slice(i, i + 8).map(async s => {
          const key = `${s.host}:${s.port}`;
          try { entries.set(key, { ...await probe(s.host, s.port), online: true }); }
          catch { entries.set(key, { ...s, online: false }); }
        }));
      }
    } finally { refreshing = false; }
  }
  let profile = { schema: 1, identities: {} };
  if (profilePath) {
    try { profile = await readJson(profilePath); }
    catch (e) { if (e.code !== 'ENOENT') throw e; }
    assert(profile.schema === 1 && profile.identities && typeof profile.identities === 'object', 'BAD_PROFILE');
  }
  if (manifest) manifest = validateManifest(manifest);
  client.on('message', msg => {
    if (msg.type === 'chat') { chats.push(msg); if (chats.length > 100) chats.shift(); }
  });
  let joining = false;
  const ui = await readFile(fileURLToPath(new URL('../web/index.html', import.meta.url)), 'utf8');
  const server = http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'");
    const reply = (code, data) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
    try {
      const address = server.address();
      const allowedHosts = [`127.0.0.1:${address.port}`, `localhost:${address.port}`];
      assert(allowedHosts.includes(req.headers.host), 'BAD_HOST');
      const url = new URL(req.url, `http://${req.headers.host}`);
      if (req.method === 'GET' && url.pathname === '/') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(ui.replace('__CSRF__', csrf)); return;
      }
      if (req.method === 'GET' && ['/app.js', '/style.css'].includes(url.pathname)) {
        res.writeHead(200, { 'Content-Type': url.pathname.endsWith('.js') ? 'text/javascript' : 'text/css' });
        res.end(await readFile(fileURLToPath(new URL('../web' + url.pathname, import.meta.url)))); return;
      }
      assert(secretEqual(req.headers['x-repopulated-token'], csrf), 'UNAUTHORIZED');
      if (req.method === 'GET' && url.pathname === '/api/state') {
        for (const [key, listing] of entries) {
          if (galaxy && ['127.0.0.1', 'localhost'].includes(listing.host) && listing.port === galaxy.port)
            entries.set(key, { ...listing, ...galaxy.listing(), online: true });
        }
        void refreshListings();
        reply(200, { host: galaxy?.lobby() ?? null, client: client.state(), chats,
          servers: [...entries.values()], manifestLoaded: !!manifest, gameAdapterIncluded: false }); return;
      }
      assert(req.method === 'POST', 'NOT_FOUND');
      assert(req.headers.origin === `http://${req.headers.host}`, 'BAD_ORIGIN');
      let bytes = 0, chunks = [];
      for await (const chunk of req) { bytes += chunk.length; assert(bytes <= 65536, 'BODY_TOO_LARGE'); chunks.push(chunk); }
      const body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      if (url.pathname === '/api/discover') {
        // Broadcast advertisements are untrusted. Verify each candidate through TCP.
        const candidates = await discover(galaxy?.options.discoveryPort || 32915);
        const results = await Promise.allSettled(candidates.slice(0, 32).map(s => probe(s.host, s.port)));
        for (const r of results) if (r.status === 'fulfilled') {
          const key = `${r.value.host}:${r.value.port}`;
          if (entries.size < 32 || entries.has(key)) entries.set(key, { ...r.value, online: true });
        }
      } else if (url.pathname === '/api/probe') {
        endpoint(body.host, body.port);
        const listing = await probe(body.host, body.port);
        assert(entries.size < 32 || entries.has(`${body.host}:${body.port}`), 'BROWSER_FULL');
        entries.set(`${body.host}:${body.port}`, { ...listing, online: true });
      } else if (url.pathname === '/api/join') {
        assert(manifest, 'MANIFEST_REQUIRED', 'Generate and load an explicit active-mod manifest first.');
        assert(!joining && !client.state().connected, 'ALREADY_CONNECTED');
        endpoint(body.host, body.port);
        joining = true;
        try {
          const key = `${body.host}:${body.port}`;
          const welcome = await client.connect({ host: body.host, port: body.port, name: body.name,
            faction: body.faction, password: body.password, manifest, resumeToken: profile.identities[key]?.resumeToken });
          profile.identities[key] = { sessionId: welcome.sessionId, resumeToken: welcome.resumeToken };
          if (profilePath) await atomicJson(profilePath, profile);
        } finally { joining = false; }
      } else if (url.pathname === '/api/disconnect') {
        client.disconnect();
      } else if (url.pathname === '/api/forget') {
        assert(!joining && !client.state().connected, 'ALREADY_CONNECTED');
        endpoint(body.host, body.port);
        delete profile.identities[`${body.host}:${body.port}`];
        if (profilePath) await atomicJson(profilePath, profile);
      } else if (url.pathname === '/api/ready') {
        client.send({ type: 'ready', ready: body.ready });
      } else if (url.pathname === '/api/chat') {
        client.send({ type: 'chat', text: body.text });
      } else if (url.pathname === '/api/settings') {
        assert(galaxy, 'NOT_HOST'); await galaxy.configure(body);
      } else if (url.pathname === '/api/start') {
        assert(galaxy, 'NOT_HOST'); galaxy.startGame();
      } else if (url.pathname === '/api/save') {
        assert(galaxy, 'NOT_HOST'); await galaxy.save();
      } else if (url.pathname === '/api/kick') {
        assert(galaxy, 'NOT_HOST'); await galaxy.kick(body.id);
      } else throw Object.assign(new Error('Not found'), { code: 'NOT_FOUND' });
      reply(200, { ok: true });
    } catch (e) {
      reply(e.code === 'NOT_FOUND' ? 404 : 400, { error: e.code ?? 'REQUEST_FAILED', message: e.code ? e.message : 'Request failed' });
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  return {
    port: server.address().port, client,
    close: async () => { client.disconnect(); server.closeAllConnections(); await new Promise(r => server.close(r)); }
  };
}
