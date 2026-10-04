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


