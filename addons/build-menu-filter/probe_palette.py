"""Probe constructor/palette functions in an isolated hidden game process."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'.runtime/python-tools'))
import frida
KNOWN='8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c'

ISOLATION=r'''
const cfg=__CONFIG__;
const shell=Process.getModuleByName('SHELL32.dll');
const alloc=new NativeFunction(Process.getModuleByName('ole32.dll').getExportByName('CoTaskMemAlloc'),'pointer',['uint64']);
Interceptor.attach(shell.getExportByName('SHGetKnownFolderPath'),{
 onEnter(args){this.out=args[3];},onLeave(ret){if(ret.toInt32()<0)throw Error('Folder redirect failed');const p=alloc((cfg.root.length+1)*2);p.writeUtf16String(cfg.root);this.out.writePointer(p);send({type:'isolated'});}
});
Interceptor.attach(shell.getExportByName('SHGetFolderPathW'),{
 onEnter(args){this.out=args[4];},onLeave(ret){if(ret.toInt32()>=0)this.out.writeUtf16String(cfg.root);}
});
const noSteam=new NativeCallback(()=>0,'int',[]);
Interceptor.replace(Process.getModuleByName('steam_api64.dll').getExportByName('SteamAPI_Init'),noSteam);
const sdl=Process.getModuleByName('SDL2.dll');
Interceptor.attach(sdl.getExportByName('SDL_CreateWindow'),{onEnter(args){args[5]=ptr((args[5].toUInt32()&~4)|8);}});
const hide=new NativeCallback(()=>{},'void',['pointer']);Interceptor.replace(sdl.getExportByName('SDL_ShowWindow'),hide);
'''

PROBE=r'''
const game=Process.getModuleByName('ReassemblyRelease.exe');
const lookup=new NativeFunction(game.base.add(0x25b2e0),'pointer',['uint']);
const setIds=new NativeFunction(game.base.add(0x9b140),'void',['pointer','pointer']);
let editor=null,palette=null,original=null,initial=null,filtered=null,originalIds=null,phase=0;
function ids(vector){const begin=vector.readPointer(),end=vector.add(8).readPointer(),count=end.sub(begin).toInt32()/4;if(!Number.isInteger(count)||count<0||count>20000)throw Error('Bad ID vector');return Array.from({length:count},(_,i)=>begin.add(i*4).readU32());}
function record(id){const sb=lookup(id);if(sb.isNull())throw Error('Unknown block '+id);const bt=sb.add(0x90).readPointer();const name=bt.add(0x10).readPointer();return {id,features:sb.add(0x28).readU64().toString(),group:bt.readS32(),name:name.isNull()?'':name.readUtf8String(),durability:bt.add(0x1c).readFloat(),capacity:sb.add(0x5c).readFloat()};}
Interceptor.attach(game.base.add(0x9d690),{onEnter(args){editor=args[0];palette=editor.add(0xd8);send({type:'constructor',editor:editor.toString(),flags:args[2].toInt32()});}});
Interceptor.attach(game.base.add(0x9b140),{
 onEnter(args){this.palette=args[0];this.list=args[1];},
 onLeave(){const list=ids(this.list);send({type:'palette-list',mode:this.palette.add(0x3c0).readU32(),count:list.length,ids:list});
 if(palette && this.palette.equals(palette) && !original){original=this.list;originalIds=list;initial=list.map(record);send({type:'metadata',blocks:initial});}}
});
Interceptor.attach(game.base.add(0x9bc50),{onEnter(args){
 if(!palette||!args[0].equals(palette)||!original)return;
 if(phase===0){const matches=initial.filter(b=>(BigInt(b.features)&((1n<<6n)|(1n<<7n)|(1n<<11n)|(1n<<15n)|(1n<<28n)))!==0n).map(b=>b.id);
 const data=Memory.alloc(Math.max(matches.length,1)*4);matches.forEach((id,i)=>data.add(i*4).writeU32(id));filtered={data,vector:Memory.alloc(24)};filtered.vector.writePointer(data);filtered.vector.add(8).writePointer(data.add(matches.length*4));filtered.vector.add(16).writePointer(data.add(Math.max(matches.length,1)*4));phase=1;setIds(palette,filtered.vector);send({type:'filtered',count:matches.length,originalUnchanged:JSON.stringify(ids(original))===JSON.stringify(originalIds)});
 }else if(phase===1){phase=2;setIds(palette,original);send({type:'restored',count:ids(original).length,originalUnchanged:JSON.stringify(ids(original))===JSON.stringify(originalIds)});}
}});
'''


def main():
    exe=Path('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe')
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=KNOWN:raise ValueError('Unsupported build')
    folder=ROOT/'research'/('profile-'+str(time.time_ns()));folder.mkdir(parents=True)
    env=dict(os.environ,USERPROFILE=str(folder),APPDATA=str(folder),LOCALAPPDATA=str(folder))
    argv=[str(exe),'kNetworkEnable=0','kHeadlessMode=0','kPauseOnLostFocus=0','kMaximizeWindow=0','kWindowSize={1280,720}',
          'kSandboxScript='+json.dumps('constructor 8; echo constructor-probe-finished')]
    device=frida.get_local_device();pid=None;messages=[];old=Path.cwd()
    try:
        os.chdir(exe.parent);pid=device.spawn(argv,env=env,stdio='pipe');session=device.attach(pid)
        script=session.create_script(ISOLATION.replace('__CONFIG__',json.dumps({'root':str(folder)}))+PROBE)
        def message(raw,data):
            row=raw.get('payload',raw);messages.append(row)
            if isinstance(row,dict) and row.get('type')=='metadata':print('metadata:',len(row['blocks']),flush=True)
            else:print(json.dumps(row),flush=True)
        script.on('message',message);script.load();device.resume(pid);time.sleep(6)
    finally:
        os.chdir(old)
        if pid is not None:
            try:device.kill(pid)
            except frida.ProcessNotFoundError:pass
            time.sleep(.2)
        (folder/'messages.json').write_text(json.dumps(messages,indent=2));print('Profile:',folder)
    if not any(isinstance(m,dict) and m.get('type')=='restored' and m.get('originalUnchanged') for m in messages):
        raise AssertionError('Palette filter/restore not proven')


if __name__=='__main__':main()
