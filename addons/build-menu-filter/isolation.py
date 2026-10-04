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
const isoSteam=Process.getModuleByName('steam_api64.dll');
let noSteam=null;
if(!cfg.steamEnabled){
 noSteam=new NativeCallback(()=>0,'int',[]);
 Interceptor.replace(isoSteam.getExportByName('SteamAPI_Init'),noSteam);
}
if(cfg.earlyStats){
 // Make the startup race deterministic without touching campaign or Cloud data.
 let slow=true;
 Interceptor.attach(Process.getModuleByName('ReassemblyRelease.exe').base.add(0x162d90),{
  onEnter(){if(slow){slow=false;Thread.sleep(2);}}
 });
 Interceptor.attach(isoSteam.getExportByName('SteamAPI_Init'),{onLeave(result){if(result.toInt32()){
  const stats=new NativeFunction(isoSteam.getExportByName('SteamAPI_SteamUserStats_v012'),'pointer',[])();
  const request=new NativeFunction(isoSteam.getExportByName('SteamAPI_ISteamUserStats_RequestCurrentStats'),'bool',['pointer']);
  send({type:'early-stats-request',ok:!!request(stats)});
 }}});
}
const sdl=Process.getModuleByName('SDL2.dll');
Interceptor.attach(sdl.getExportByName('SDL_CreateWindow'),{onEnter(args){args[5]=ptr((args[5].toUInt32()&~4)|8);}});
const hide=new NativeCallback(()=>{},'void',['pointer']);Interceptor.replace(sdl.getExportByName('SDL_ShowWindow'),hide);
'''


