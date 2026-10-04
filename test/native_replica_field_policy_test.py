from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for private native field installation')
class NativeReplicaFieldTests(unittest.TestCase):
    def test_private_field_installation_checks_signature_and_reverts_failed_configuration(self):
        function=SCRIPT[SCRIPT.index('function installReplicaField('):SCRIPT.index('function comparisonViewSettings(')]
        code=r'''
const assert=require('assert');
const cfg={nativeCampaignClient:false},calls=[],zone={},campaignConsole={},dll={getExportByName:name=>name};
let replicaFieldStatsReader=null;
let signatureValid=true,configureResult=1;
const getter={readByteArray:()=>Uint8Array.from(signatureValid?[0x40,0x53,0x56,0x57,0x48,0x83,0xec,0x30,0xc5,0xf8,0x29,0x74,0x24,0x20]:[0]).buffer};
const game={base:{add:rva=>{assert.strictEqual(rva,0xcab60);return getter;}}};
const Interceptor={replaceFast:(pointer,exported)=>{assert.strictEqual(pointer,getter);calls.push(exported);return 'original';},revert:pointer=>{assert.strictEqual(pointer,getter);calls.push('revert');}};
const NativeFunction=function(name){
 if(name==='RepopulatedSetFieldOriginal')return pointer=>{assert.strictEqual(pointer,'original');calls.push('set-original');};
 if(name==='RepopulatedConfigureReplicaField')return (console,actualZone)=>{assert.strictEqual(console,campaignConsole);assert.strictEqual(actualZone,zone);calls.push('configure');return configureResult;};
 if(name==='RepopulatedReplicaFieldStats')return ()=>{};
 throw Error(name);
};
__FUNCTION__
installReplicaField(dll,zone);assert.deepStrictEqual(calls,[]);
cfg.nativeCampaignClient=true;signatureValid=false;assert.throws(()=>installReplicaField(dll,zone),/signature differs/);assert.deepStrictEqual(calls,[]);
signatureValid=true;configureResult=-3;assert.throws(()=>installReplicaField(dll,zone),/configuration failed: -3/);
assert.deepStrictEqual(calls,['RepopulatedReplicaField','set-original','configure','revert']);
calls.length=0;configureResult=1;installReplicaField(dll,zone);
assert.deepStrictEqual(calls,['RepopulatedReplicaField','set-original','configure']);
'''.replace('__FUNCTION__',function)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
        # The independent background renderer must always see the campaign streamer.
        self.assertNotIn('zone.add(0x248).writePointer',SCRIPT)
        self.assertIn('if(cfg.worldReplica && !cfg.nativeCampaignClient) Interceptor.attach(game.base.add(0xcab60)',SCRIPT)


if __name__=='__main__':unittest.main()
