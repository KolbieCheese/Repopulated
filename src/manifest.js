import { createHash } from 'node:crypto';
import { readdir, readFile, lstat, realpath } from 'node:fs/promises';
import path from 'node:path';
import { assert, validateManifest } from './protocol.js';

async function hashTree(root) {
  root = await realpath(root);
  const entries = [];
  async function visit(dir) {
    for (const name of (await readdir(dir)).sort()) {
      const absolute = path.join(dir, name);
      const stat = await lstat(absolute);
      assert(!stat.isSymbolicLink(), 'SYMLINK_MOD', absolute);
      if (stat.isDirectory()) await visit(absolute);
      else if (stat.isFile()) entries.push([path.relative(root, absolute).split(path.sep).join('/'), absolute]);
    }
  }
  await visit(root);
  const hash = createHash('sha256');
  for (const [relative, absolute] of entries.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)) {
    const bytes = await readFile(absolute);
    // Length-delimited paths and bytes avoid concatenation ambiguities.
    hash.update(JSON.stringify([relative, bytes.length]) + '\n'); hash.update(bytes);
  }
  return hash.digest('hex');
}

export async function buildManifest(spec) {
  assert(spec && typeof spec.gameExe === 'string' && Array.isArray(spec.mods), 'BAD_MANIFEST_SPEC');
  const hash = createHash('sha256').update(await readFile(spec.gameExe)).digest('hex');
  const mods = [];
  for (const mod of spec.mods) {
    assert(typeof mod.id === 'string' && typeof mod.path === 'string', 'BAD_MANIFEST_SPEC');
    mods.push({ id: mod.id, hash: await hashTree(mod.path) });
  }
  return validateManifest({ schema: 1, gameHash: hash, mods });
}
