/* Reassembly's Steam callback thread starts before its stats table is ready.
 * Frida's suspended startup can expose this race (null read at RVA 489f9).
 * Leave Steam initialized and keep its callbacks queued until the table exists.
 * This is a readiness check, not a timeout or a change to Cloud/save settings.
 */
const startupGame = Process.getModuleByName('ReassemblyRelease.exe');
for (const [rva, expected] of Object.entries({
  '49660': '40534883ec2065488b042558000000ba',
  '489c0': '48895c24185556574154415541564157'
})) {
  const actual = new Uint8Array(startupGame.base.add(parseInt(rva, 16)).readByteArray(expected.length / 2));
  if (Array.from(actual, b => b.toString(16).padStart(2, '0')).join('') !== expected)
    throw Error('Unsupported Steam startup signature ' + rva);
}
const startupStatsTable = startupGame.base.add(0x3d43a8); // singleton + 0xb8
const startupRunCallbacks = Process.getModuleByName('steam_api64.dll').getExportByName('SteamAPI_RunCallbacks');
let startupOriginal, startupDeferred = 0, startupReady = false, startupSteam = null;
Interceptor.attach(Process.getModuleByName('steam_api64.dll').getExportByName('SteamAPI_Init'), {
  onLeave(result) {
    startupSteam = result.toInt32() !== 0;
    send({type: 'steam-initialized', ok: startupSteam});
  }
});
const startupCallback = new NativeCallback(() => {
  if (!startupReady) {
    if (startupStatsTable.readPointer().isNull() || startupStatsTable.add(8).readPointer().isNull()) {
      if (++startupDeferred === 1) send({type: 'steam-startup-waiting'});
      return;
    }
    startupReady = true;
    send({type: 'steam-startup-ready', deferredCallbacks: startupDeferred});
  }
  startupOriginal();
}, 'void', []);
startupOriginal = new NativeFunction(Interceptor.replaceFast(startupRunCallbacks, startupCallback), 'void', [], {exceptions: 'propagate'});
