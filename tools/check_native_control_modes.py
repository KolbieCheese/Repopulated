"""Verify native campaign loading/navigation for all three control schemes.

This tests neutral native input and saved configuration, not synthetic keys.
Physical binding and menu transitions still require a hands-on playtest.
"""
import argparse
import json
import math
from pathlib import Path
import time
from native_session import NativeSession
from scene_codec import Reader
from native_coop import CoopHost,run_client


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--remote',action='store_true',help='Also check each mode in an authoritative host/client session')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]/'.runtime'/('native-modes-'+str(time.time_ns()));root.mkdir()
    results=[]
    for mode in ('MOUSE_ROT','KEY_ROT','CARDINAL'):
        failures=[];saved=[];intents=[];folder=root/mode;game=None
        def message(record):
            if record.get('type')=='error':failures.append(record.get('description','Native error'))
            if record.get('type')=='campaign-native-save':saved.append(record)
            if record.get('type')=='native-navigation-intent':intents.append(record)
        try:
            game=NativeSession(args.exe,folder,{'networkControl':True,'controlScheme':mode,'captureNativeIntent':True,
                 'campaignSaveProbe':True,'sampleIntervalMs':500,'maxSamples':30},message,campaign=True)
            game.resume();deadline=time.monotonic()+15
            while time.monotonic()<deadline and not failures and not saved:time.sleep(0.1)
            if failures or not saved or not intents:raise RuntimeError('Native mode failed: '+mode+' '+str(failures))
            fields=Reader((folder/'Reassembly'/'data'/'save0'/'save.lua').read_bytes()).value()['fields']
            if fields.get('controlScheme')!=mode:raise RuntimeError('Native mode was not preserved: '+mode)
            if any(not all(math.isfinite(m[key]) for key in ('vx','vy','angle')) or m['ident']!=0x70000001 for m in intents):
                raise RuntimeError('Invalid native navigation intent')
            results.append({'mode':mode,'savedMode':fields['controlScheme'],'nativeIntentSamples':len(intents),
                            'lastIntent':intents[-1],'failures':failures})
        finally:
            if game:game.close()
    remote=[]
    if args.remote:
        for mode in ('MOUSE_ROT','KEY_ROT','CARDINAL'):
            host=None
            try:
                host=CoopHost(args.exe,root/('remote-host-'+mode),('127.0.0.1',0))
                result=run_client(args.exe,root/('remote-client-'+mode),('127.0.0.1',host.server.server_address[1]),host.token,
                                  seconds=10,test_controls=True,control_scheme=mode)
                fields=Reader((root/('remote-client-'+mode)/'Reassembly'/'data'/'save0'/'save.lua').read_bytes()).value()['fields']
                if fields.get('controlScheme')!=mode or result['failures'] or host.failures or result['updates']<3 or host.maximum_movement<20:
                    raise RuntimeError('Remote native control mode failed: '+mode+' '+str(result['failures']+host.failures))
                remote.append(dict(result,hostFailures=host.failures,maximumMovement=host.maximum_movement,savedMode=fields['controlScheme']))
            finally:
                if host:host.close()
    report={'nativeModeLoadValidated':True,'physicalControlAndMenuTransitionsValidated':False,'results':results,'remoteResults':remote}
    (root/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
