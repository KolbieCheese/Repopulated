"""Check byte-identical native ship serialization after reconstruction."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    args=parser.parse_args()
    exe=args.exe.resolve()
    root=Path(__file__).resolve().parents[1]
    folder=root / '.runtime' / ('cluster-check-'+str(time.time_ns()))
    folder.mkdir(parents=True)
    source=exe.parent.parent / 'data' / 'ships' / '8_interceptor.lua'
    reports=[]
    payloads=[]
    for index in range(2):
        report=folder / f'run-{index}.json'
        script=folder / f'script-{index}.txt'
        script.write_text(f'import {source.as_posix()}; sleep 1; echo finished')
        subprocess.run([sys.executable,str(root / 'tools' / 'run_native_probe.py'),
                        '--exe',str(exe),'--seconds','5','--headless',
                        '--sandbox-file',str(script),'--cluster-export-test',
                        '--report-file',str(report)],check=True)
        summary=json.loads(report.read_text())
        reports.append(summary)
        source=Path(summary['artifactDirectory']) / 'exported-cluster.lua'
        payloads.append(source.read_bytes())
    if b'blocks={' not in payloads[0] or b'command={' not in payloads[0]:
        raise RuntimeError('Serialized ship lacks block or command payload')
    if payloads[0]!=payloads[1]:
        raise RuntimeError('Reconstructed native ship serialization differs')
    result={'schema':1,'nativeClusterRoundTripValidated':True,'campaignValidated':False,
            'multiplayerValidated':False,'bytes':len(payloads[0]),
            'sha256':hashlib.sha256(payloads[0]).hexdigest(),'runs':reports}
    (folder / 'result.json').write_text(json.dumps(result,indent=2))
    print('Native ship reconstruction is byte-identical. Evidence:',folder / 'result.json')


if __name__=='__main__':
    main()
