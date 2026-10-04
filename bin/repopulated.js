import path from 'node:path';
import { writeFile, mkdir } from 'node:fs/promises';
import { GalaxyServer } from '../src/server.js';
import { launchWeb } from '../src/web.js';
import { buildManifest } from '../src/manifest.js';
import { readJson, atomicJson } from '../src/storage.js';
import { token } from '../src/protocol.js';

const args = process.argv.slice(2);
const command = args.shift() ?? 'help';
const flag = name => {
  const i = args.indexOf(name);
  if (i < 0) return null;
  if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing value for ${name}`);
  return args[i + 1];
};
try {
  if (command === 'manifest') {
    const spec = flag('--spec'), out = flag('--out');
    if (!spec || !out) throw new Error('Use manifest --spec <JSON> --out <JSON>');
    const input = await readJson(spec);
    const base = path.dirname(path.resolve(spec));
    input.gameExe = path.resolve(base, input.gameExe);
    input.mods = input.mods.map(m => ({ ...m, path: path.resolve(base, m.path) }));
    const manifest = await buildManifest(input);
    await atomicJson(out, manifest);
    console.log(`Manifest written: ${path.resolve(out)} (${manifest.mods.length} ordered mods)`);
  } else if (command === 'server' || command === 'browser') {
    let galaxy, web;
    const cleanup = async () => { await web?.close(); await galaxy?.close(); };
    if (command === 'server') {
      const file = flag('--config') ?? 'config/server.example.json';
      const config = await readJson(file);
      const manifest = await readJson(config.manifestPath);
      const engineToken = token();
      galaxy = new GalaxyServer({ ...config, manifest, engineToken, password: process.env.REPOPULATED_PASSWORD });
      galaxy.on('warning', message => console.warn(message));
      await galaxy.listen();
      const credentials = '.runtime/engine-credentials.json';
      await mkdir('.runtime', { recursive: true });
      await writeFile(credentials, JSON.stringify({ host: '127.0.0.1', port: galaxy.port, token: engineToken }, null, 2), { mode: 0o600 });
      try {
        if (!args.includes('--no-web')) web = await launchWeb({ galaxy, port: config.webPort ?? 32914,
          manifest, profilePath: '.runtime/client-profile.json' });
      } catch (e) { await cleanup(); throw e; }
      console.log(`Transport listening on ${config.bind ?? '0.0.0.0'}:${galaxy.port}`);
      console.log('Native Reassembly adapter is NOT implemented; this build cannot run multiplayer gameplay.');
    } else {
      const manifestPath = flag('--manifest');
      web = await launchWeb({ port: Number(flag('--port') ?? 32914),
        manifest: manifestPath ? await readJson(manifestPath) : null,
        profilePath: flag('--profile') ?? '.runtime/client-profile.json' });
    }
    if (web) console.log(`Launcher: http://127.0.0.1:${web.port}`);
    let stopping = false;
    for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
      if (stopping) return; stopping = true;
      try { await cleanup(); process.exitCode = 0; }
      catch (e) { console.error(e.message); process.exitCode = 1; }
    });
  } else {
    console.log(`Repopulated experimental transport (no native game adapter yet)

node bin/repopulated.js manifest --spec config/mods.json --out .runtime/manifest.json
node bin/repopulated.js server --config config/server.example.json [--no-web]
node bin/repopulated.js browser --manifest .runtime/manifest.json [--port 32914]

Use REPOPULATED_PASSWORD to protect joins. LAN UDP discovery: 32915; game TCP: 32913.
The browser/controller listens on loopback only. No public listing service or NAT relay is included.`);
  }
} catch (e) { console.error(`${e.code ?? 'ERROR'}: ${e.message}`); process.exitCode = 1; }
