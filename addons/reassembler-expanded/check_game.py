"""Bounded real-engine load check with an isolated profile and Steam disabled.

Requires Frida in the multiplayer research .runtime/python-tools (testing only).
No multiplayer DLL or engine patch is loaded. No player save is opened.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / '.runtime/python-tools'))
import frida

SCRIPT = r'''
const cfg = __CONFIG__;
const shell = Process.getModuleByName('SHELL32.dll');
const ole = Process.getModuleByName('ole32.dll');
const alloc = new NativeFunction(ole.getExportByName('CoTaskMemAlloc'),'pointer',['uint64']);
Interceptor.attach(shell.getExportByName('SHGetKnownFolderPath'), {
  onEnter(args) { this.out=args[3]; },
  onLeave(ret) {
    if(ret.toInt32()<0) throw Error('Folder redirection failed');
    const path=alloc((cfg.root.length+1)*2); path.writeUtf16String(cfg.root);
    this.out.writePointer(path); send('isolated');
  }
});
const legacy=shell.findExportByName('SHGetFolderPathW');
if(legacy) Interceptor.attach(legacy,{
  onEnter(args){this.out=args[4];},
  onLeave(ret){if(ret.toInt32()>=0)this.out.writeUtf16String(cfg.root);}
});
const noSteam=new NativeCallback(()=>0,'int',[]);
Interceptor.replace(Process.getModuleByName('steam_api64.dll').getExportByName('SteamAPI_Init'),noSteam);
const sdl=Process.getModuleByName('SDL2.dll');
Interceptor.attach(sdl.getExportByName('SDL_CreateWindow'), {
  onEnter(args){args[5]=ptr((args[5].toUInt32() & ~4) | 8);}
});
const hidden=new NativeCallback(()=>{},'void',['pointer']);
Interceptor.replace(sdl.getExportByName('SDL_ShowWindow'),hidden);
const game=Process.getModuleByName('ReassemblyRelease.exe');
const getClusters=new NativeFunction(game.getExportByName('?getClusters@GameZone@@QEBAAEBV?$vector@PEAUBlockCluster@@V?$allocator@PEAUBlockCluster@@@std@@@std@@XZ'),'pointer',['pointer']);
const metrics={};
for(const [key,name] of Object.entries({generation:'getPowerPerSec',powerStorage:'getPowerCapacity',resources:'getResourceCapacity',mass:'getMass'}))
  metrics[key]=new NativeFunction(game.getExportByName('?'+name+'@BlockCluster@@QEBAMXZ'),'float',['pointer']);
const health=new NativeFunction(game.getExportByName('?getMaxHealth@Block@@QEBAMXZ'),'float',['pointer']);
const mass=new NativeFunction(game.getExportByName('?getMass@Block@@QEBAMXZ'),'float',['pointer']);
let reported=false;
function inspectCluster(cluster){
  if(reported || cluster.isNull())return;
  const blocks=cluster.add(0xf0), ids=[], values=[];
  for(let b=blocks.readPointer();b.compare(blocks.add(8).readPointer())<0;b=b.add(8)){
    const block=b.readPointer(), id=block.add(0x18).readU32();
    ids.push(id); values.push({id,mass:mass(block),health:health(block)});
  }
  if(ids.includes(17000)&&ids.includes(17001)){
    const result={type:'runtime-stats',blocks:values};
    for(const [key,fn] of Object.entries(metrics))result[key]=fn(cluster);
    send(result);reported=true;
  }
}
Interceptor.attach(game.getExportByName('?getDeadliness@BlockCluster@@QEBAHXZ'),{
  onEnter(args){this.cluster=args[0];},onLeave(){inspectCluster(this.cluster);}
});
for(const name of ['?addToGameZone@BlockCluster@@QEAAXPEAUGameZone@@U?$tvec2@M$0A@@glm@@@Z',
                  '?addToGameZone@BlockCluster@@QEAAXPEAUGameZone@@U?$tvec2@M$0A@@glm@@M@Z'])
  Interceptor.attach(game.getExportByName(name),{
    onEnter(args){this.cluster=args[0];},onLeave(){inspectCluster(this.cluster);}
  });
Interceptor.attach(game.getExportByName('?Update@GameZone@@QEAAXXZ'),{
  onEnter(args){this.zone=args[0];},
  onLeave(){
    if(reported)return;
    const vector=getClusters(this.zone), end=vector.add(8).readPointer();
    for(let at=vector.readPointer();at.compare(end)<0;at=at.add(8)){
      inspectCluster(at.readPointer());
      if(reported)break;
    }
  }
});
'''


def main():
    exe = Path('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe')
    if hashlib.sha256(exe.read_bytes()).hexdigest() != '8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c':
        raise ValueError('Unknown game build; do not instrument it')
    root = ROOT / 'test-runtime' / str(time.time_ns())
    mod = root / 'Reassembly/mods/reassembler-expanded'
    mod.mkdir(parents=True)
    for name in ('blocks.lua', 'shapes.lua'):
        shutil.copyfile(ROOT / 'generated' / name, mod / name)
    (mod.parent / 'index.lua').write_text('{{name="reassembler-expanded",type=LOCAL,enabled=1}}')
    fixture_dir = root / 'fixture'
    fixture_dir.mkdir()
    fixture = fixture_dir / 'fixture.lua'
    fixture.write_text('{blocks={{800,{0,0}},{17000,{45,0}},{17001,{125,0}}}}')
    env = dict(os.environ)
    env.update(USERPROFILE=str(root), APPDATA=str(root), LOCALAPPDATA=str(root))
    device, pid = frida.get_local_device(), None
    messages = []
    old_cwd = Path.cwd()
    try:
        os.chdir(exe.parent)
        commands = f'import {fixture_dir.as_posix()}; activate; echo expansion-check-finished'
        argv = [str(exe), 'kNetworkEnable=0', 'kHeadlessMode=0', 'kWriteBlocks=1', 'kPauseOnLostFocus=0',
                'kSandboxScript=' + json.dumps(commands)]
        pid = device.spawn(argv, env=env, stdio='pipe')
        session = device.attach(pid)
        script = session.create_script(SCRIPT.replace('__CONFIG__', json.dumps({'root':str(root)})))
        script.on('message', lambda message, data: messages.append(message))
        script.load()
        device.resume(pid)
        log = root / 'Reassembly/data/log_latest.txt'
        for _ in range(25):
            time.sleep(.2)
            try:
                if log.exists() and ('Goodbye!' in log.read_text(errors='replace') or 'expansion-check-finished' in log.read_text(errors='replace')):
                    break
            except PermissionError:
                pass
        if pid is not None:
            try:
                device.kill(pid)
            except frida.ProcessNotFoundError:
                pass
            pid = None
        for _ in range(20):
            try:
                text = log.read_text(errors='replace')
                break
            except PermissionError:
                time.sleep(.1)
        print('Profile:', root)
        print('Isolation messages:', messages)
        if log.exists():
            selected = [line for line in text.splitlines() if any(word in line.lower() for word in
                        ('error:', 'warning:', '[mods]', 'loaded 1551', 'unknown id'))]
            for line in selected:
                print(line[:1200])
            if "Loaded 1551 blocks" not in text or 'COMPLETED WITH ERRORS' in text or 'Unknown Id' in text:
                raise AssertionError('Mod load validation failed')
            stats = next((m['payload'] for m in messages if isinstance(m.get('payload'), dict) and m['payload'].get('type') == 'runtime-stats'), None)
            if stats is None:
                raise AssertionError('No runtime statistics from the fixture')
            config=json.loads((ROOT/'config.json').read_text())
            by_id={b['id']:b for b in stats['blocks']}
            for ident, section in ((17000,'reactor'),(17001,'storage')):
                part=config[section]
                assert abs(by_id[ident]['mass']-part['width']**2*part['density'])<.1
                assert abs(by_id[ident]['health']-part['width']**2*part['durability'])<.1
            assert abs(stats['generation'] - (config['reactor']['generation']+300)) < .1
            assert abs(stats['powerStorage'] - (config['reactor']['powerStorage']+900)) < .1
            assert abs(stats['resources'] - (config['storage']['capacity']+100)) < .1
            (root/'validation.json').write_text(json.dumps(stats,indent=2)+'\n')
            print('PASS: block loading, connected fixture, runtime output/storage/mass/health')
        else:
            raise RuntimeError('No isolated log produced')
    finally:
        os.chdir(old_cwd)
        if pid is not None:
            try:
                device.kill(pid)
            except frida.ProcessNotFoundError:
                pass


if __name__ == '__main__':
    main()
