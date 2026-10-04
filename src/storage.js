import { mkdir, readFile, writeFile, rename, copyFile } from 'node:fs/promises';
import path from 'node:path';
import { token } from './protocol.js';

export async function readJson(file) { return JSON.parse(await readFile(file, 'utf8')); }
export async function atomicJson(file, data) {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = file + '.' + token().slice(0, 12) + '.tmp';
  await writeFile(temporary, JSON.stringify(data, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  try { await copyFile(file, file + '.bak'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  await rename(temporary, file);
}
