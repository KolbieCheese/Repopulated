from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for private native diagnostics')
class NativeThrustAuditPolicyTests(unittest.TestCase):
    def test_audit_gates_hooks_and_drains_only_valid_bounded_rows(self):
        functions=SCRIPT[SCRIPT.index('function thrustAuditSettings('):SCRIPT.index('function installReplicaField(')]
        code=r'''
const assert=require('assert');
class Pointer{
 constructor(bytes,offset=0){this.bytes=bytes;this.offset=offset;}
 add(offset){return new Pointer(this.bytes,this.offset+offset);}
 readDouble(){return new DataView(this.bytes).getFloat64(this.offset,true);}
 writeDouble(value){new DataView(this.bytes).setFloat64(this.offset,value,true);}
}
const Memory={alloc:length=>new Pointer(new ArrayBuffer(length))};
const cfg={renderOnly:false},records=[],configured=[],hooks=[],reverts=[],dll={getExportByName:name=>name};
let thrustAuditIdent=0,thrustAuditReader=null,thrustAuditBuffer=null,thrustAuditStatsBuffer=null,
    lastThrustAuditCenter=null,lastThrustAuditReport=0,thrustAuditBusySkips=0,readCount=0,configureResult=1;
let particleRenderAuditReader=null,particleRenderAuditBuffer=null,lastParticleRenderAuditCalls=0,particleRenderAuditBusySkips=0,particleReady=0;
let localExhaustStatsReader=null,localExhaustStatsBuffer=Memory.alloc(40);
let particleSignatureValid=true,particleSetterFails=false;
const game={base:{add:rva=>({rva,readByteArray:()=>Uint8Array.from(rva===0x458c0?
 (particleSignatureValid?[0x48,0x89,0x5c,0x24,0x20,0x4c,0x89,0x44,0x24,0x18,0x48,0x89,0x54,0x24,0x10]:[0]):
 [0x48,0x8b,0xc4,0x53,0x57,0x48,0x81,0xec,0x08,0x01,0x00,0x00]).buffer})}};
const Interceptor={replaceFast:(address,name)=>{hooks.push([address.rva,name]);return 'trampoline';},revert:address=>reverts.push(address.rva)};
const NativeFunction=function(name){
 if(name==='RepopulatedConfigureThrustAudit')return (ident,only)=>{assert.strictEqual(typeof only,'number');assert([0,1].includes(only));configured.push([ident,only]);return configureResult;};
 if(['RepopulatedSetAuditMoverOriginal','RepopulatedSetParticleRenderAuditOriginal'].includes(name))return original=>{
  assert.strictEqual(original,'trampoline');
  if(name==='RepopulatedSetParticleRenderAuditOriginal' && particleSetterFails)throw Error('setter failed');
 };
 if(name==='RepopulatedReadParticleRenderAudit')return buffer=>{buffer.add(15*8).writeDouble(10);return particleReady;};
 if(name==='RepopulatedReadThrustAudit')return (buffer,capacity,stats)=>{
  assert.strictEqual(capacity,128);[24,0,0,28].forEach((value,i)=>stats.add(i*8).writeDouble(value));return readCount;
 };
 throw Error(name);
};
const Process={getCurrentThreadId:()=>7},send=record=>records.push(record),displayFrames=12,presentationDelayMs=100;
__FUNCTIONS__
assert.strictEqual(thrustAuditSettings(undefined),0);assert.strictEqual(thrustAuditSettings(null),0);
assert.strictEqual(thrustAuditSettings(0x70000002),0x70000002);
for(const invalid of [false,true,0,0x70000001,'1879048194'])assert.throws(()=>thrustAuditSettings(invalid),/Invalid native thrust audit fixture/);
configureThrustAudit(dll);installHostThrustAudit(dll);reportThrustAudit();assert.strictEqual(configured.length,0);assert.strictEqual(hooks.length,0);
thrustAuditIdent=0x70000002;configureResult=-2;assert.throws(()=>configureThrustAudit(dll),/configuration failed: -2/);assert.strictEqual(hooks.length,0);
configureResult=1;configureThrustAudit(dll);assert.deepStrictEqual(configured.at(-1),[0x70000002,1]);
assert.strictEqual(thrustAuditBuffer.bytes.byteLength,128*28*8);
installHostThrustAudit(dll);assert.deepStrictEqual(hooks,[[0x458c0,'RepopulatedAuditParticleRender'],[0xf09e0,'RepopulatedAuditMoverUpdate']]);
cfg.renderOnly=true;installHostThrustAudit(dll);assert.strictEqual(hooks.length,2);
thrustAuditReader=null;configureThrustAudit(dll);assert.deepStrictEqual(configured.at(-1),[0x70000002,0]);
readCount=-7;reportThrustAudit();assert.strictEqual(thrustAuditBusySkips,1);assert.strictEqual(records.length,0);
readCount=129;assert.throws(()=>reportThrustAudit(),/read failed: 129/);
readCount=1;thrustAuditBuffer.add(8).writeDouble(0x70000002);thrustAuditBuffer.add(16).writeDouble(0x78001234);
reportThrustAudit([3000,3000]);assert.strictEqual(records.length,1);assert.strictEqual(records[0].rows[0].length,28);
assert.deepStrictEqual(records[0].center,[3000,3000]);assert.strictEqual(records[0].stats.stride,28);
thrustAuditBuffer.add(8).writeDouble(0x70000001);assert.throws(()=>reportThrustAudit(),/Invalid native thrust audit row/);
thrustAuditBuffer.add(8).writeDouble(0x70000002);thrustAuditBuffer.add(16).writeDouble(0);assert.throws(()=>reportThrustAudit(),/Invalid native thrust audit row/);
particleReady=-7;reportParticleRenderAudit();assert.strictEqual(particleRenderAuditBusySkips,1);
particleReady=-2;assert.throws(()=>reportParticleRenderAudit(),/read failed: -2/);
particleReady=1;reportParticleRenderAudit();assert.strictEqual(records.length,2);
assert.strictEqual(records[1].type,'particle-render-audit');assert.strictEqual(records[1].row.length,18);
reportParticleRenderAudit();assert.strictEqual(records.length,2); // A previous native draw snapshot is not emitted twice.
particleRenderAuditBuffer.writeDouble(NaN);assert.throws(()=>reportParticleRenderAudit(),/Invalid native particle render audit row/);
thrustAuditReader=null;particleSignatureValid=false;const hookCount=hooks.length;
assert.throws(()=>configureThrustAudit(dll),/particle render audit signature differs/);assert.strictEqual(hooks.length,hookCount);
thrustAuditReader=null;particleSignatureValid=true;particleSetterFails=true;
assert.throws(()=>configureThrustAudit(dll),/setter failed/);assert.deepStrictEqual(reverts,[0x458c0]);
assert.deepStrictEqual(readLocalExhaustStats(),{});
localExhaustStatsReader=buffer=>[100,2,3,4,5].forEach((value,i)=>buffer.add(i*8).writeDouble(value));
assert.deepStrictEqual(readLocalExhaustStats(),{transformed:100,snapshotUnavailable:2,staleCurveSkipped:3,publicationMisses:4,invalidRootSkipped:5});
localExhaustStatsReader=buffer=>buffer.writeDouble(NaN);assert.throws(()=>readLocalExhaustStats(),/Invalid native local exhaust stats/);
'''.replace('__FUNCTIONS__',functions)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)


if __name__=='__main__':unittest.main()
