"""Package a console-free desktop launcher with Python, Tk, Frida and our DLL."""
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BUILD_TOOLS=ROOT/'.runtime'/'desktop-build-tools'
FRIDA_TOOLS=ROOT/'.runtime'/'python-tools'
sys.path[:0]=[str(BUILD_TOOLS),str(FRIDA_TOOLS)]


def main():
    if not (ROOT/'.runtime'/'RepopulatedDiagnostic.dll').is_file():raise RuntimeError('Build the native DLL first')
    env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(BUILD_TOOLS),str(FRIDA_TOOLS))))
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed',
        '--name','Repopulated Multiplayer','--distpath',str(ROOT/'.runtime'/'desktop-dist'),
        '--workpath',str(ROOT/'.runtime'/'desktop-build'),'--specpath',str(ROOT/'.runtime'/'desktop-build'),
        '--paths',str(ROOT/'tools'),'--paths',str(FRIDA_TOOLS),'--collect-all','frida',
        '--add-data',str(ROOT/'.runtime'/'RepopulatedDiagnostic.dll')+os.pathsep+'.',
        str(ROOT/'tools'/'native_desktop.py')],env=env,check=True)
    package=ROOT/'.runtime'/'desktop-dist'/'Repopulated Multiplayer'
    licenses=package/'licenses';licenses.mkdir(exist_ok=True)
    for name in ('frida','PyInstaller'):
        dist=importlib.metadata.distribution(name)
        for file in dist.files or []:
            if file.name.startswith(('LICENSE','COPYING')):shutil.copyfile(dist.locate_file(file),licenses/(name+'-'+file.name))
    python=Path(sys.base_prefix)
    shutil.copyfile(python/'LICENSE.txt',licenses/'Python-LICENSE.txt')
    for library in ('tcl8.6','tk8.6'):
        source=python/'tcl'/library/'license.terms'
        if source.is_file():shutil.copyfile(source,licenses/(library+'-license.terms'))
    (licenses/'README.txt').write_text('Bundled components: Python, Tcl/Tk, Frida and the PyInstaller bootloader. Original license texts accompany this package.\n',encoding='utf-8')
    (package/'README.txt').write_text(
        'REPOPULATED MULTIPLAYER — EXPERIMENTAL WINDOWS ALPHA\n\n'
        'Extract the entire folder, then run Repopulated Multiplayer.exe.\n'
        'Choose your installed win64/ReassemblyRelease.exe. Host and Join are in one desktop window.\n'
        'Stock content and two players only. The executable/content hashes must match.\n'
        'Share the host LAN address, game port and generated join token.\n'
        'Leave Native campaign controls and HUD enabled on Join. Choose the initial rotation mode.\n'
        'Both games use native Player controls; the host decides movement, firing, damage and spawning.\n'
        'The client retains unchanged ships and interpolates motion on a source-time buffer.\n'
        'The 100 ms presentation buffer smooths arrivals and adds visible control delay.\n'
        'Immediate local input replay remains experimental and disabled.\n'
        'The client generates native exhaust locally from thruster controls.\n'
        'Movement, weapons, projectiles and damage share a frequent state stream; geometry is separate.\n'
        'Native client visuals include projectile trails, turret rotation and beams.\n'
        'The native map receives authoritative faction colours and discovered station/objective markers.\n'
        'Exploration is separate by default. Hosts can check Share exploration between players before hosting.\n'
        'Both discovery records and the sharing setting survive save/resume.\n'
        'Map and Binding overlays keep receiving world updates on both sides.\n'
        'Update both launchers together; native protocol version 7 rejects older alpha clients.\n'
        'Save current galaxy creates a checkpoint; select it before hosting again to resume.\n'
        'State and test profiles are in %LOCALAPPDATA%\\Repopulated; normal Reassembly saves are untouched.\n'
        'Closing the launcher stops its own game sessions. Direct game TCP port:32916; LAN discovery UDP:32917.\n\n'
        'This is not complete faction multiplayer. Native flight/HUD is experimental.\n'
        'Remote building, Upgrade and Fleet actions are disabled until authoritative transactions exist.\n'
        'Native aiming, mode changes, bindings and menus still need hands-on two-PC testing.\n'
        'This limited campaign has per-seat discovery; full vanilla galaxy bootstrap,\n'
        'territory scoring and synchronized map navigation remain unfinished.\n'
        'Remote ship editing, complete client menus, faction choice, mods, full remote\n'
        'progression and dedicated campaign servers remain unfinished.\n'
        'Explosions, impacts, charge glows and shields still need event/state triggers and local visual checks.\n'
        'Save/resume currently preserves\n'
        'the host campaign and both faction ships. Two-machine smoothness needs further testing.\n'
        'See source/docs/native-evening-test.md for the playtest steps and known limits.\n'
        'No Reassembly executable or game assets are included. Source and detailed notes are included.\n',encoding='utf-8')
    for folder,pattern in (('tools','*.py'),('native','*.c'),('native','*.h'),('docs','native*.md')):
        destination=package/'source'/folder;destination.mkdir(parents=True,exist_ok=True)
        for file in (ROOT/folder).glob(pattern):shutil.copyfile(file,destination/file.name)
    release=ROOT/'.runtime'/'releases';release.mkdir(exist_ok=True);archive=release/'Repopulated-Multiplayer-Alpha.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as output:
        for file in sorted(package.rglob('*')):
            if file.is_file():output.write(file,Path(package.name)/file.relative_to(package))
    print(archive)


if __name__=='__main__':main()
