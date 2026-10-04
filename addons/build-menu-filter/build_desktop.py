"""Build the console-free Windows launcher, including its private Python runtime."""
from pathlib import Path
import subprocess
import sys
import importlib.metadata
import shutil

ROOT=Path(__file__).resolve().parent
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed',
    '--name','Reassembly Filters','--distpath',str(ROOT/'desktop-dist'),
    '--workpath',str(ROOT/'desktop-build'),'--specpath',str(ROOT/'desktop-build'),
    '--hidden-import','lua_data','--hidden-import','isolation','--hidden-import','PIL.ImageGrab',
    str(ROOT/'launcher.py')],check=True)

licenses=ROOT/'desktop-dist/Reassembly Filters/licenses';licenses.mkdir(exist_ok=True)
for package in ('frida','Pillow','PyInstaller'):
    dist=importlib.metadata.distribution(package)
    for name in dist.files or []:
        if name.name.startswith(('LICENSE','COPYING')):
            shutil.copyfile(dist.locate_file(name),licenses/(package+'-'+name.name))
python_root=Path(sys.base_prefix)
shutil.copyfile(python_root/'LICENSE.txt',licenses/'Python-LICENSE.txt')
shutil.copyfile(python_root/'tcl/tk8.6/license.terms',licenses/'Tk-license.terms')
(licenses/'README.txt').write_text('Bundled components: Python, Tcl/Tk, Frida, Pillow, and the PyInstaller bootloader.\n'
    'Original component license texts are included here. Frida project: https://frida.re\n'
    'Python project: https://www.python.org  Pillow: https://python-pillow.org\n'
    'PyInstaller: https://pyinstaller.org  Tcl/Tk: https://www.tcl.tk\n',encoding='utf-8')

import package_release
package_release.main()
