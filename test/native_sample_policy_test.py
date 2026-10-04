import ast
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


class NativeSamplePolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node is required for sample selection')
    def test_only_explicit_optout_changes_sampler_and_preserves_count_cadence(self):
        line=next(line.strip() for line in SCRIPT.splitlines() if 'sampler = new NativeFunction' in line)
        code=r'''
const assert=require('assert'),zone={};let sampler=null;
let exportName=null;
const Process={getModuleByName:()=>({getExportByName:name=>name})};
const NativeFunction=function(name,result,args){
 exportName=name;assert.strictEqual(result,'int');assert.deepStrictEqual(args,['pointer']);
 return actual=>{assert.strictEqual(actual,zone);return 123;};
};
for(const diagnosticSamples of [undefined,true,false,0,null,'false']){
 const cfg={diagnosticSamples};__BIND__
 assert.strictEqual(exportName,diagnosticSamples===false?'RepopulatedCountNativeClusters':'RepopulatedSample');
 let sampled=9;__SAMPLE__
 assert.strictEqual(result,123);assert.strictEqual(sampled,10); // World IDs and fixture/bootstrap thresholds retain their original cadence.
}
'''.replace('__BIND__',line).replace('__SAMPLE__','const result = sampler(zone); sampled++;')
        self.assertIn('const result = sampler(zone); sampled++;',SCRIPT)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_normal_host_and_client_disable_disk_diagnostics(self):
        source=Path(__file__).resolve().parents[1]/'tools/native_coop.py'
        configs=[]
        for node in ast.walk(ast.parse(source.read_text(encoding='utf8'))):
            if not isinstance(node,ast.Dict):continue
            values={key.value:value for key,value in zip(node.keys,node.values) if isinstance(key,ast.Constant)}
            if 'diagnosticSamples' in values:
                configs.append(values)
                self.assertIs(values['diagnosticSamples'].value,False)
        self.assertEqual(len(configs),2)
        self.assertEqual(sum('campaignRemote' in values for values in configs),1)
        self.assertEqual(sum('renderOnly' in values for values in configs),1)


if __name__=='__main__':unittest.main()
