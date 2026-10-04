"""Extract the release and test its frozen GUI/backend using private game profiles."""
import json
from pathlib import Path
import subprocess
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'.runtime'/('package-check-'+str(time.time_ns()));folder.mkdir()
    archive=ROOT/'.runtime'/'releases'/'Repopulated-Multiplayer-Alpha.zip'
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if folder.resolve() not in (folder/name).resolve().parents:raise ValueError('Unsafe archive path')
        if source.testzip():raise ValueError('Archive checksum failure')
        source.extractall(folder)
    exe=folder/'Repopulated Multiplayer'/'Repopulated Multiplayer.exe'
    reports={}
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    for mode in ('self-check','native-smoke'):
        report=folder/(mode+'.json')
        result=subprocess.run([str(exe),'--'+mode,'--report',str(report)],startupinfo=startup,timeout=60)
        if result.returncode or not report.is_file():raise RuntimeError('Packaged '+mode+' failed: '+str(result.returncode))
        reports[mode]=json.loads(report.read_text())
        if reports[mode].get('error'):raise RuntimeError(str(reports[mode]['error']))
    (folder/'result.json').write_text(json.dumps(reports,indent=2));print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
