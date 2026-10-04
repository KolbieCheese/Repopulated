"""Launch Reassembly with a build-menu filter, or verify it in an isolated profile."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
try:
    import frida
except ImportError:
    # Developer convenience; release users install the local venv with Setup.cmd.
    sys.path.insert(0,str(ROOT.parents[1]/'.runtime/python-tools'))
    import frida

KNOWN='8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c'

def save_layout(settings_path,changed):
    updated=json.loads(settings_path.read_text())
    updated.update(toolbarX=changed['x'],toolbarY=changed['y'],toolbarScale=changed['scale'])
    temporary=settings_path.with_name(settings_path.name+'.'+str(time.time_ns())+'.tmp')
    temporary.write_text(json.dumps(updated,indent=2)+'\n');temporary.replace(settings_path)


def find_game():
    roots=[Path('D:/SteamLibrary/steamapps/common/Reassembly')]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as key:
            steam=Path(winreg.QueryValueEx(key,'SteamPath')[0])
        roots.append(steam/'steamapps/common/Reassembly')
        libraries=steam/'steamapps/libraryfolders.vdf'
        if libraries.exists():
            for value in re.findall(r'"path"\s*"([^"\n]+)"',libraries.read_text(encoding='utf-8')):
                roots.append(Path(value.replace('\\\\','\\'))/'steamapps/common/Reassembly')
    except (OSError,ImportError):pass
    return next((r/'win64/ReassemblyRelease.exe' for r in roots if (r/'win64/ReassemblyRelease.exe').exists()),None)


def labels_and_provenance(exe, optional_provenance=True):
    import lua_data as build
    faction_labels={}
    provenance={}
    def label(value):
        return ''.join(json.loads(s) for s in build.TOKEN.findall(str(value)) if s.startswith('"')).strip()
    def factions(path):
        return {int(k):label(build.get(v,'name',str(k))) for k,v in build.parse(build.read_data(path)) if k is not None and isinstance(v,list)}
    faction_labels.update(factions(exe.parent.parent/'data/factions.lua'))
    expanded=Path.home()/'Saved Games/Reassembly/mods/reassembler-expanded'
    if not optional_provenance or not (expanded/'build-report.json').exists():return faction_labels,provenance
    report=json.loads((expanded/'build-report.json').read_text())
    registry=json.loads((expanded/'registry.json').read_text())
    provenance['17000']={'key':'custom','label':'Reassembler Expanded'}
    provenance['17001']={'key':'custom','label':'Reassembler Expanded'}
    workshop=exe.parents[2]/'workshop/content/329130'
    for source in report['sourceWorkshopIds']:
        path=workshop/source
        if not (path/'blocks.lua').exists():continue
        blocks={build.number(b[0][1]):b for b in build.records(path/'blocks.lua')}
        names=factions(path/'factions.lua') if (path/'factions.lua').exists() else {}
        def group(ident,seen=()):
            if ident in seen or ident not in blocks:return 0
            own=build.get(blocks[ident],'group')
            parent=build.get(blocks[ident],'extends')
            return build.number(own) if own is not None else group(build.number(parent),seen+(ident,)) if parent is not None else 0
        for key,new_id in registry['blocks'].items():
            mod,old_id=key.split(':')
            if mod!=source:continue
            original_group=group(int(old_id))
            source_label=names.get(original_group,faction_labels.get(original_group,'Faction '+str(original_group)))
            provenance[str(new_id)]={'key':source+':'+str(original_group),'label':source_label}
    return faction_labels,provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,default=find_game(),help='Auto-detected Steam installation, or explicit executable path')
    parser.add_argument('--no-provenance',action='store_true',help='Use ordinary faction metadata only, without optional add-on provenance')
    parser.add_argument('--background',action='store_true',help='Console-free graphical-launcher backend')
    parser.add_argument('--test',action='store_true',help='Fresh profile, hidden window, disabled Steam/network, bounded automated checks')
    parser.add_argument('--steam-test',action='store_true',help='With --test: real Steam and an early-stats regression; Cloud disabled in private profile')
    parser.add_argument('--addon-test',action='store_true',help='Test the installed expanded faction in the isolated profile')
    args=parser.parse_args()
    if args.exe is None:parser.error('Reassembly not found; pass --exe with the Windows x64 executable path')
    if args.addon_test and not args.test:parser.error('--addon-test requires --test')
    if args.steam_test and not args.test:parser.error('--steam-test requires --test')
    exe=args.exe.resolve()
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=KNOWN:raise ValueError('Unsupported Reassembly executable; no hooks installed')
    cfg={'settings':json.loads((ROOT/'settings.json').read_text()),'signatures':json.loads((ROOT/'signatures.json').read_text()),'testing':args.test}
    try:
        cfg['factionLabels'],cfg['provenance']=labels_and_provenance(exe,not args.no_provenance)
    except (OSError,ValueError,KeyError,IndexError) as error:
        print('Optional source-label integration unavailable:',error,flush=True)
        cfg['factionLabels'],cfg['provenance']=labels_and_provenance(exe,False)
    source=(ROOT/'startup.js').read_text()+(ROOT/'model.js').read_text()+(ROOT/'runtime.js').read_text().replace('__CONFIG__',json.dumps(cfg))
    folder=ROOT/'research'/('test-'+str(time.time_ns())) if args.test else None
    env=dict(os.environ);argv=[str(exe)];faction=8
    if args.test:
        from isolation import ISOLATION
        folder.mkdir(parents=True)
        env.update(USERPROFILE=str(folder),APPDATA=str(folder),LOCALAPPDATA=str(folder))
        source=ISOLATION.replace('cfg','isoCfg').replace('sdl','isoSdl').replace('__CONFIG__',json.dumps({'root':str(folder),'steamEnabled':args.steam_test,'earlyStats':args.steam_test}))+source
        if args.addon_test:
            addon=Path.home()/'Saved Games/Reassembly/mods/reassembler-expanded'
            target=folder/'Reassembly/mods/reassembler-expanded'
            shutil.copytree(addon,target)
            faction=json.loads((addon/'build-report.json').read_text())['targetFaction']
            (target/'factions.lua').write_text('{'+str(faction)+'={name="Filter Test",playable=2}}')
        argv+=['kSteamCloudEnable=0','kNetworkEnable=0','kHeadlessMode=0','kPauseOnLostFocus=0','kMaximizeWindow=0','kWindowSize={1280,720}',
               'kSandboxScript='+json.dumps(f'constructor {faction}; echo filter-test')]
    device=frida.get_local_device();pid=None;messages=[];old=Path.cwd();detached=[]
    try:
        if getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None) # Keep bundled launcher DLLs out of the game's DLL search path.
        os.chdir(exe.parent);pid=device.spawn(argv,env=env,stdio='pipe' if args.test or args.background else 'inherit');session=device.attach(pid)
        session.on('detached',lambda reason,crash:detached.append((reason,crash)))
        script=session.create_script(source)
        def message(raw,data):
            row=raw.get('payload',raw);messages.append(row);print(json.dumps(row),flush=True)
            if isinstance(row,dict) and row.get('type')=='layout-changed' and not args.test:
                save_layout(ROOT/'settings.json',row['layout'])
            if isinstance(row,dict) and row.get('type')=='screenshot' and data and folder:
                from PIL import Image
                image=Image.frombytes('RGBA',(row['width'],row['height']),data)
                image.transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(folder/(row['name']+'.png'))
        script.on('message',message);script.load()
        if any(isinstance(m,dict) and m.get('type')=='error' for m in messages):
            raise RuntimeError('Extension startup failed; see the error above. The game was not resumed.')
        device.resume(pid)
        if args.test:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                status=script.exports_sync.status()
                if status['active'] and status['frames']>0:break
                time.sleep(.1)
            else:raise RuntimeError('Test builder did not become ready')
            def settle():
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    current=script.exports_sync.status()
                    if not current['pending']:return current
                    time.sleep(.05)
                raise RuntimeError('Game did not apply the pending palette update')
            if args.steam_test:
                assert any(m.get('type')=='steam-startup-waiting' for m in messages if isinstance(m,dict))
                assert any(m.get('type')=='steam-startup-ready' and m['deferredCallbacks']>0 for m in messages if isinstance(m,dict))
                assert any(m.get('type')=='early-stats-request' and m['ok'] for m in messages if isinstance(m,dict))
                steam=script.exports_sync.steamstatus()
                assert steam['initialized'] and steam['callbacksReady']
                print('STEAM_READ_ONLY',json.dumps(steam),flush=True)
            def sendevent(value):
                if 'x' in value and value['x']<1200:
                    g=script.exports_sync.status()['geometry']
                    value=dict(value,x=round(g['x']+(value['x']-220)*g['scale']),y=round(g['y']+(value['y']-12)*g['scale']))
                return script.exports_sync.testevent(value)
            script.exports_sync.capture('toolbar-all');time.sleep(.2)
            for value in [
                {'category':'Weapons','source':'All','query':''},
                {'category':'Thrusters','source':'All','query':''},
                {'category':'All','source':'All','query':'this-part-does-not-exist'},
                {'category':'All','source':'All','query':''}]:
                script.exports_sync.setfilter(value);settle()
            status=script.exports_sync.status();print('STATUS',json.dumps(status),flush=True)
            assert status['frames']>0 and status['failures']==0 and status['shown']==status['total'] and status['total']>0
            checks=[m for m in messages if isinstance(m,dict) and m.get('type')=='filter-applied']
            assert any(m['category']=='Weapons' and 0<m['shown']<m['total'] for m in checks)
            assert any(m['query']=='this-part-does-not-exist' and m['shown']==0 for m in checks)
            # Read the actual displayed ID vector back from the game and check
            # every numeric sort in both directions with an independent oracle.
            metrics=['mass','health','cost','area','generation','powerStorage','resources','thrust','shieldHealth','dps','range','buildTime']
            for metric in metrics:
                for direction in ('asc','desc'):
                    script.exports_sync.setsort({'metric':metric,'direction':direction});settle()
                    items=script.exports_sync.testitems();values=[b['stats'][metric] for b in items]
                    known=[v for v in values if v is not None]
                    assert known==sorted(known,reverse=direction=='desc'), (metric,direction,known)
                    assert values==known+[None]*(len(values)-len(known)), 'Missing values must remain last'
                    assert len(items)==status['total']
            if args.addon_test:
                stats_by_id={b['id']:b['stats'] for b in script.exports_sync.testitems()}
                reactor=stats_by_id[17000];vault=stats_by_id[17001]
                assert reactor['mass']==1920 and reactor['health']==9600 and reactor['cost']==3000
                assert reactor['generation']==10000 and reactor['powerStorage']==30000
                assert vault['mass']==1280 and vault['health']==12800 and vault['resources']==30000
            for filtered_category,filtered_metric in [('Weapons','dps'),('Reactors','generation')]:
                script.exports_sync.setfilter({'category':filtered_category,'source':'All','query':''})
                script.exports_sync.setsort({'metric':filtered_metric,'direction':'desc'});settle()
                subset=script.exports_sync.testitems()
                assert 0<len(subset)<status['total'] and all(filtered_category in b['categories'] for b in subset)
                values=[b['stats'][filtered_metric] for b in subset if b['stats'][filtered_metric] is not None]
                assert values==sorted(values,reverse=True)
            script.exports_sync.capture('sorted-reactors');time.sleep(.15)
            script.exports_sync.setfilter({'category':'All','source':'All','query':''})
            script.exports_sync.setsort({'metric':'Default','direction':'desc'});time.sleep(.15)
            sendevent({'type':0x300,'key':1073741889})
            sendevent({'type':0x301,'key':1073741889});time.sleep(.15)
            assert script.exports_sync.status()['metric']=='name'
            sendevent({'type':0x300,'key':1073741889,'mods':1})
            sendevent({'type':0x301,'key':1073741889,'mods':1});time.sleep(.15)
            assert script.exports_sync.status()['direction']=='desc'
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':250,'y':132})
            time.sleep(.15);script.exports_sync.capture('sort-menu');time.sleep(.15)
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':300,'y':230})
            time.sleep(.15);assert script.exports_sync.status()['metric']=='mass'
            script.exports_sync.capture('sorted-mass');time.sleep(.15)
            script.exports_sync.setsort({'metric':'Default','direction':'desc'});time.sleep(.15)
            # Exercise the real SDL event path, not only the filter RPC.
            sendevent({'type':0x300,'key':1073741887});time.sleep(.2)
            assert script.exports_sync.status()['category']=='Weapons'
            sendevent({'type':0x301,'key':1073741887});
            sendevent({'type':0x300,'key':102,'mods':0x40});
            sendevent({'type':0x303,'text':'zzz-no-match'});time.sleep(.2)
            assert script.exports_sync.status()['shown']==0
            sendevent({'type':0x300,'key':27});time.sleep(.1)
            script.exports_sync.setfilter({'category':'All','source':'All','query':''});time.sleep(.2)
            values=script.exports_sync.sources()
            if args.addon_test:
                assert len(values)>2
                script.exports_sync.setfilter({'category':'All','source':values[1]['key'],'query':''});time.sleep(.2)
                subset=script.exports_sync.status();assert 0<subset['shown']<subset['total']
            script.exports_sync.setfilter({'category':'Reactors','source':'All','query':''});time.sleep(.2)
            script.exports_sync.capture('toolbar-reactors');time.sleep(.2)
            script.exports_sync.setfilter({'category':'All','source':'All','query':''});time.sleep(.2)
            # Mouse coordinates use logical SDL pixels, matching the toolbar.
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':420,'y':48})
            time.sleep(.2)
            assert script.exports_sync.status()['category']=='Weapons'
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':1080,'y':102})
            time.sleep(.2)
            reset=script.exports_sync.status()
            assert reset['category']=='All' and reset['shown']==reset['total']
            sendevent({'type':0x300,'key':1073741888})
            sendevent({'type':0x301,'key':1073741888});time.sleep(.2)
            assert not script.exports_sync.status()['visible']
            script.exports_sync.capture('toolbar-collapsed');time.sleep(.2)
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':250,'y':24})
            time.sleep(.2)
            assert script.exports_sync.status()['visible']
            g=script.exports_sync.status()['geometry']
            assert status['cursorFrames']>0 and abs(status['geometry']['x']-(status['geometry']['screenWidth']-status['geometry']['width'])/2)<1
            for kind,dx,dy in [('move',80,60),('resize',-180,0)]:
                before=script.exports_sync.status()['geometry']
                x=round(before['x']+(100 if kind=='move' else 890)*before['scale'])
                y=round(before['y']+(10 if kind=='move' else 136)*before['scale'])
                script.exports_sync.testevent({'type':0x401,'x':x,'y':y})
                script.exports_sync.testevent({'type':0x400,'x':x+dx,'y':y+dy})
                script.exports_sync.testevent({'type':0x402,'x':x+dx,'y':y+dy});time.sleep(.25)
                after=script.exports_sync.status()['geometry']
                if kind=='move':assert abs(after['x']-before['x']-dx)<2 and abs(after['y']-before['y']-dy)<2
                else:assert after['scale']<before['scale'] and after['width']<before['width']
            # Resized controls must still accept matching physical mouse coordinates.
            for event_type in (0x401,0x402):sendevent({'type':event_type,'x':420,'y':48})
            time.sleep(.2);assert script.exports_sync.status()['category']=='Weapons'
            script.exports_sync.capture('moved-resized');time.sleep(.2)
            changed=next(m['layout'] for m in reversed(messages) if isinstance(m,dict) and m.get('type')=='layout-changed')
            layout_fixture=folder/'saved-layout-settings.json';layout_fixture.write_text(json.dumps(cfg['settings']))
            save_layout(layout_fixture,changed)
            persisted=json.loads(layout_fixture.read_text())
            assert persisted['toolbarX']==changed['x'] and persisted['toolbarScale']==changed['scale']
            before=script.exports_sync.status()['geometry']
            x=round(before['x']+100*before['scale']);y=round(before['y']+10*before['scale'])
            script.exports_sync.testevent({'type':0x401,'x':x,'y':y,'clicks':2})
            script.exports_sync.testevent({'type':0x402,'x':x,'y':y});time.sleep(.25)
            reset_geometry=script.exports_sync.status()['geometry']
            assert reset_geometry['scale']==1 and abs(reset_geometry['x']-(reset_geometry['screenWidth']-900)/2)<1
            script.exports_sync.setfilter({'category':'Weapons','source':'All','query':''});time.sleep(.2)
            for event_type in (0x401,0x402):
                sendevent({'type':event_type,'x':1830,'y':940})
            time.sleep(.3)
            assert not script.exports_sync.status()['active'], 'Edit palette must restore the original list'
            sendevent({'type':0x300,'key':27})
            sendevent({'type':0x301,'key':27});time.sleep(.3)
            returned=script.exports_sync.status()
            assert returned['active'] and returned['category']=='Weapons' and 0<returned['shown']<returned['total'] and returned['failures']==0
            print('LIFECYCLE',json.dumps(returned),flush=True)
            print('PASS: toolbar, filters, all numeric sorts both directions, missing values last, SDL keyboard/mouse, toggle, Edit Palette, original IDs unchanged')
        else:
            print('Launching Reassembly; waiting for Steam and game initialization…',flush=True)
            ready=False
            while True:
                time.sleep(.25)
                if any(isinstance(m,dict) and m.get('type')=='steam-initialized' and not m['ok'] for m in messages):
                    raise RuntimeError('Steam could not initialize. Start Steam and sign in, then launch again. The launcher stopped before opening an empty local save list.')
                if not ready and any(isinstance(m,dict) and m.get('type')=='steam-startup-ready' for m in messages):
                    ready=True
                    print(json.dumps({'type':'game-ready'}),flush=True)
                    print('Reassembly started with Steam enabled. Filters appear in the builder. Close the game normally to save.',flush=True)
                try:script.exports_sync.status()
                except (frida.InvalidOperationError,frida.TransportError):
                    if not ready:raise RuntimeError('Reassembly exited before finishing startup. Check its latest crash log and session.log.')
                    if any(crash for _,crash in detached):raise RuntimeError('Reassembly crashed. Check its latest crash log and session.log.')
                    break
    except KeyboardInterrupt:
        pass
    finally:
        os.chdir(old)
        if pid is not None:
            try:device.kill(pid)
            except (frida.ProcessNotFoundError,frida.InvalidOperationError):pass
        if folder:
            (folder/'messages.json').write_text(json.dumps(messages,indent=2)+'\n');print('Test profile:',folder)


if __name__=='__main__':main()

