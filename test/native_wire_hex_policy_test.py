from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for wire hex policy tests')
class NativeWireHexPolicyTests(unittest.TestCase):
    def check_js(self,body):
        helpers=SCRIPT[SCRIPT.index('function installWireHex('):SCRIPT.index('function retainVisualFrame(')]
        fixture=r'''
const assert=require('assert');
let wireHexDecode=null,wireHexEncode=null,available=true,missing=false,decodeResult=null,encodeResult=null;
let allocations=0,lookups=0,bindings=0;const calls=[];
function point(bytes,offset=0){return {
 add:delta=>point(bytes,offset+delta),
 writeByteArray:source=>bytes.set(source,offset),
 readByteArray:length=>bytes.slice(offset,offset+length).buffer,
 readUtf8String:length=>Buffer.from(bytes.slice(offset,offset+length)).toString('utf8'),
 bytes,offset
};}
const Memory={alloc:size=>{allocations++;return point(new Uint8Array(size));},
 allocUtf8String:text=>{const result=Memory.alloc(Buffer.byteLength(text)+1);result.writeByteArray(Buffer.from(text));return result;}};
const module={getExportByName:name=>{lookups++;if(missing)throw Error('Missing required codec export');return name;}};
const Process={findModuleByName:name=>{assert.strictEqual(name,'RepopulatedDiagnostic.dll');return available?module:null;}};
const NativeFunction=function(name,result,args){
 bindings++;assert.strictEqual(result,'int');assert.deepStrictEqual(args,['pointer','int','pointer','int']);
 if(name==='RepopulatedDecodeHex')return (source,length,out,capacity)=>{
  calls.push(['decode',length,capacity]);assert.strictEqual(typeof length,'number');assert.strictEqual(typeof capacity,'number');
  if(decodeResult!==null)return decodeResult;
  const text=source.readUtf8String(length);if(!/^[a-f0-9]*$/.test(text))return -2;
  assert(capacity>=length/2);out.writeByteArray(Buffer.from(text,'hex'));return length/2;
 };
 assert.strictEqual(name,'RepopulatedEncodeHex');return (source,length,out,capacity)=>{
  calls.push(['encode',length,capacity]);assert.strictEqual(capacity,length*2+1);assert(out.bytes.length>=capacity);
  if(encodeResult!==null)return encodeResult;
  const text=Buffer.from(source.readByteArray(length)).toString('hex');out.writeByteArray(Buffer.from(text+'\0'));return text.length;
 };
};
__HELPERS__
'''.replace('__HELPERS__',helpers)
        checked=subprocess.run([shutil.which('node'),'-e',fixture+body],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_cached_native_owned_buffers_and_terminator_capacity(self):
        self.check_js(r'''
const raw=packedBuffer('0001feff');assert.deepStrictEqual(Array.from(new Uint8Array(raw.readByteArray(4))),[0,1,254,255]);
assert.strictEqual(hexBuffer(raw,4),'0001feff');
assert.strictEqual(hexBuffer(packedBuffer(''),0),'');
assert.deepStrictEqual(calls,[['decode',8,4],['encode',4,9],['decode',0,0],['encode',0,1]]);
assert.strictEqual(bindings,2);assert.strictEqual(lookups,2);
assert.strictEqual(hexBuffer(packedBuffer('10'),1),'10');assert.strictEqual(bindings,2);
const maximum='01'.repeat(1048576);
assert.strictEqual(hexBuffer(packedBuffer(maximum),1048576),maximum); // Maximum health packet, including the trailing NUL allocation.
''')

    def test_strict_bounds_lowercase_and_native_partial_failures(self):
        self.check_js(r'''
for(const bad of [null,10,{},'0','00'.repeat(1048577)])assert.throws(()=>packedBuffer(bad),/Invalid packed hex bounds/);
assert.strictEqual(allocations,0);assert.strictEqual(bindings,0);
for(const bad of [-1,1.5,NaN,Infinity,'1',1048577])assert.throws(()=>hexBuffer(null,bad),/Invalid native hex source bounds/);
for(const bad of ['AB','0g','é0'])assert.throws(()=>packedBuffer(bad),/Native packed hex decode failed: -2/);
for(const result of [-1,-2,0,3]){decodeResult=result;assert.throws(()=>packedBuffer('0102'),/Native packed hex decode failed/);}
decodeResult=null;const raw=packedBuffer('0102');
for(const result of [-1,-3,0,2]){encodeResult=result;assert.throws(()=>hexBuffer(raw,2),/Native packed hex encode failed/);}
''')

    def test_fallback_is_only_available_before_the_module_loads(self):
        self.check_js(r'''
available=false;
const raw=packedBuffer('00ff');assert.strictEqual(hexBuffer(raw,2),'00ff');assert.strictEqual(bindings,0);
assert.strictEqual(hexBuffer(packedBuffer(''),0),'');
for(const bad of ['AB','0g'])assert.throws(()=>packedBuffer(bad),/Invalid packed hex data/);
available=true;missing=true;
assert.throws(()=>packedBuffer('00'),/Missing required codec export/); // A loaded incompatible DLL must fail, rather than silently taking the slow path.
missing=false;assert.strictEqual(hexBuffer(packedBuffer('0a'),1),'0a');
assert(calls.some(row=>row[0]==='decode'));
''')

    def test_world_preparation_is_complete_before_queue_commit_and_not_in_geometry(self):
        start=SCRIPT.index('}, applyworld(world) {')+len('}, applyworld(world) ')
        handler=SCRIPT[start:SCRIPT.index('}, applystate(state)',start)]+ '} '
        geometry=SCRIPT[SCRIPT.index('    if(worldLoader && pendingWorld && consoleContext) {'):SCRIPT.index('      let campaignMap={};')]
        self.assertNotIn('packedBuffer(plan.poses)',geometry)
        self.assertNotIn('packedBuffer(plan.health)',geometry)
        self.check_js(r'''
const cfg={worldReplica:true,persistentReplica:true,sandbox:'private',syncCampaignMap:true,replicatePresentation:false};
let worldSeq=0,pendingWorld={seq:0};const replicaContinuity=()=>{},timings=[],timing=(name,ms)=>timings.push([name,ms]);
function applyworld(world)__HANDLER__
function frame(seq){return {seq,path:'private/world.lua',hasAdditions:false,
 incremental:{remove:[],poses:'00'.repeat(40),health:'00'.repeat(16)},
 map:{width:1,radius:'00'.repeat(8),cells:'00'.repeat(12),regions:'',objectives:''}};}
const first=frame(1);applyworld(first);
assert.strictEqual(worldSeq,1);assert.strictEqual(pendingWorld,first);
assert.strictEqual(first.preparedIncremental.poses.count,1);assert.strictEqual(first.preparedIncremental.health.count,1);
assert.strictEqual(first.preparedIncremental.poses.buffer.bytes.length,40);
const before=calls.length,invalid=frame(2);invalid.map.cells='invalid';
assert.throws(()=>applyworld(invalid),/Invalid packed campaign map/);
assert.strictEqual(calls.length,before);assert.strictEqual(pendingWorld,first);assert.strictEqual(worldSeq,1);
decodeResult=-2;const rejected=frame(2);
assert.throws(()=>applyworld(rejected),/Native packed hex decode failed/);
assert.strictEqual(pendingWorld,first);assert.strictEqual(worldSeq,1);
decodeResult=null;const next=frame(3);applyworld(next);
assert.strictEqual(pendingWorld,next);assert.strictEqual(worldSeq,3);
assert.notStrictEqual(first.preparedIncremental.poses.buffer,next.preparedIncremental.poses.buffer);
assert.deepStrictEqual(Array.from(first.preparedIncremental.poses.buffer.bytes),Array(40).fill(0));
assert.strictEqual(timings.length,2);assert(timings.every(row=>row[0]==='worldPrepare' && row[1]>=0));
'''.replace('__HANDLER__',handler))


if __name__=='__main__':unittest.main()
