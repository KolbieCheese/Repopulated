"""Create a standalone source/runtime-install package without other repo files."""
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
FILES=['Launch Filters.cmd','Open Launcher.vbs','Setup.cmd','README.md','requirements.txt','launch.py','launcher.py',
       'model.js','runtime.js','startup.js','settings.json','signatures.json','lua_data.py',
       'isolation.py','validation.json']

def main():
    output=ROOT/'dist/Reassembly-Build-Menu-Filters.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:archive.write(ROOT/name,'Reassembly-Build-Menu-Filters/'+name)
        desktop=ROOT/'desktop-dist/Reassembly Filters'
        if desktop.exists():
            for path in sorted(desktop.rglob('*')):
                if path.is_file():archive.write(path,'Reassembly-Build-Menu-Filters/'+path.relative_to(desktop).as_posix())
        archive.writestr('Reassembly-Build-Menu-Filters/README.txt',(ROOT/'README.md').read_text(encoding='utf-8'))
    print(output)
    versioned=output.with_name('Reassembly-Build-Menu-Filters-1.1.zip')
    import shutil
    shutil.copyfile(output,versioned)
    print(versioned)

if __name__=='__main__':main()
