from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required for the test-only native comparison view')
class NativeComparisonViewTests(unittest.TestCase):
    def test_copied_view_tracks_actual_render_point_without_mutating_native_input_view(self):
        functions=SCRIPT[SCRIPT.index('function comparisonViewSettings('):SCRIPT.index('function tracePresentation(')]
        code=r'''
const assert=require('assert');
class Pointer{
 constructor(bytes,offset=0){this.bytes=bytes;this.offset=offset;}
 add(offset){return new Pointer(this.bytes,this.offset+offset);}
 readFloat(){return new DataView(this.bytes).getFloat32(this.offset,true);}
 writeFloat(value){new DataView(this.bytes).setFloat32(this.offset,value,true);}
 readDouble(){return new DataView(this.bytes).getFloat64(this.offset,true);}
 writeDouble(value){new DataView(this.bytes).setFloat64(this.offset,value,true);}
 readByteArray(length){return this.bytes.slice(this.offset,this.offset+length);}
 writeByteArray(bytes){new Uint8Array(this.bytes,this.offset,bytes.byteLength).set(new Uint8Array(bytes));}
}
const Memory={alloc:length=>new Pointer(new ArrayBuffer(length))};
let comparisonViewConfig=null,comparisonFocus=null,lastComparisonView=null,comparisonFrameLimit=false,loaded=true,count=0,traceCalls=0;
const comparisonViewCounts={applied:0,held:0,busy:0,missing:0,focusReads:0,fallbacks:0};
const motionTraceBuffer=Memory.alloc(42*8),motionTraceReader=()=>{traceCalls++;return count;};
let focusResult=0;
const comparisonFocusBuffer=Memory.alloc(16),comparisonFocusReader=()=>focusResult,presentationStageClock=()=>1235.6;
__FUNCTIONS__
assert.strictEqual(comparisonViewSettings(undefined),null);
assert.strictEqual(comparisonFrameLimitSettings(undefined,null),false);
assert.strictEqual(comparisonFrameLimitSettings(false,null),false);
assert.throws(()=>comparisonFrameLimitSettings(true,null),/requires the comparison view fixture/);
assert.throws(()=>comparisonFrameLimitSettings(60,{ident:0x70000002,zoom:2}),/requires the comparison view fixture/);
assert.strictEqual(comparisonFrameLimitSettings(true,{ident:0x70000002,zoom:2}),true);
for(const config of [{ident:1,zoom:2},{ident:0x70000002,zoom:0},{ident:0x70000002,zoom:NaN},
                     {ident:0x70000002,zoom:1e100},{ident:0x70000002,zoom:2,extra:1}])
 assert.throws(()=>comparisonViewSettings(config),/Invalid comparison view fixture/);
const view=Memory.alloc(72);
for(let i=0;i<18;i++)view.add(i*4).writeFloat(i+0.25);
[1280,720,1280,720].forEach((value,i)=>view.add(i*4).writeFloat(value));
const original=Array.from(new Uint8Array(view.bytes));
assert.strictEqual(applyComparisonView(null,view,{}),view);assert.strictEqual(traceCalls,0);
comparisonViewConfig=comparisonViewSettings({ident:0x70000002,zoom:2});
count=-7;const waiting={};assert.strictEqual(applyComparisonView(null,view,waiting),view);
assert.strictEqual(waiting.comparisonView.status,'fallback-native-busy');assert.strictEqual(waiting.comparisonView.applied,false);
motionTraceBuffer.add(20*8).writeDouble(0x70000002);
motionTraceBuffer.add(22*8).writeDouble(1234.5);
motionTraceBuffer.add(36*8).writeDouble(500.125);motionTraceBuffer.add(37*8).writeDouble(-22.25);
motionTraceBuffer.add(40*8).writeDouble(3000);motionTraceBuffer.add(41*8).writeDouble(3000);
count=1;const context={};const copied=applyComparisonView(null,view,context); // A valid second slot can be the only ship.
assert.notStrictEqual(copied,view);assert.strictEqual(context.spectatorView,copied);
assert.strictEqual(copied.add(0x10).readFloat(),3500.125);assert.strictEqual(copied.add(0x14).readFloat(),2977.75);
for(const offset of [0x18,0x1c,0x2c])assert.strictEqual(copied.add(offset).readFloat(),0);
assert.strictEqual(copied.add(0x20).readFloat(),2);assert.strictEqual(copied.add(0x28).readFloat(),1);
const changed=new Set([0x10,0x14,0x18,0x1c,0x20,0x28,0x2c]);
for(let offset=0;offset<72;offset+=4)if(!changed.has(offset))assert.strictEqual(copied.add(offset).readFloat(),view.add(offset).readFloat());
assert.deepStrictEqual(Array.from(new Uint8Array(view.bytes)),original);
assert.strictEqual(context.comparisonView.status,'target');assert.deepStrictEqual(context.comparisonView.orientation,[1,0]);
count=-7;focusResult=1;comparisonFocusBuffer.writeDouble(3550.5);comparisonFocusBuffer.add(8).writeDouble(2970.25);
const fresh={};const busyCopy=applyComparisonView(null,view,fresh);
assert.strictEqual(fresh.comparisonView.status,'target-busy');assert.strictEqual(busyCopy.add(0x10).readFloat(),3550.5);
assert.strictEqual(busyCopy.add(0x14).readFloat(),2970.25); // Helper coordinates already include local center.
// No trace row can mean a live root with stale source poses. Read its current
// validated body, preserve the requested camera and never reuse old focus.
count=0;comparisonFocusBuffer.writeDouble(3600.875);comparisonFocusBuffer.add(8).writeDouble(3040.25);
const staleTrace={};const staleCopy=applyComparisonView(null,view,staleTrace);
assert.strictEqual(staleTrace.comparisonView.status,'target-missing');assert.strictEqual(staleTrace.comparisonView.applied,true);
assert.strictEqual(staleCopy.add(0x10).readFloat(),3600.875);assert.strictEqual(staleCopy.add(0x14).readFloat(),3040.25);
assert.strictEqual(staleCopy.add(0x20).readFloat(),2);assert.strictEqual(staleTrace.comparisonView.counts.held,0);
// A valid trace for another actor must not substitute that actor's position.
count=1;motionTraceBuffer.add(20*8).writeDouble(0);motionTraceBuffer.writeDouble(0x70000001);
comparisonFocusBuffer.writeDouble(3700.375);comparisonFocusBuffer.add(8).writeDouble(3090.5);
const otherTrace={};const otherCopy=applyComparisonView(null,view,otherTrace);
assert.strictEqual(otherTrace.comparisonView.status,'target-missing');assert.strictEqual(otherCopy.add(0x10).readFloat(),3700.375);
assert.strictEqual(otherCopy.add(0x14).readFloat(),3090.5);assert.strictEqual(otherCopy.add(0x20).readFloat(),2);
count=0;focusResult=0;const missing={};assert.strictEqual(applyComparisonView(null,view,missing),view); // A truly absent root stays native.
assert.strictEqual(missing.comparisonView.status,'fallback-native-missing');assert.strictEqual(comparisonFocus,null);
assert.strictEqual(missing.comparisonView.counts.held,0);assert.strictEqual(missing.comparisonView.x,view.add(0x10).readFloat());
count=-7;focusResult=0;const absent={};assert.strictEqual(applyComparisonView(null,view,absent),view);
assert.strictEqual(absent.comparisonView.status,'fallback-native-busy');assert.strictEqual(comparisonFocus,null);
assert.deepStrictEqual(Array.from(new Uint8Array(view.bytes)),original);
focusResult=-2;assert.throws(()=>applyComparisonView(null,view,{}),/Native comparison focus failed: -2/);
count=-3;assert.throws(()=>applyComparisonView(null,view,{}),/Native comparison view trace failed: -3/);
count=1;view.add(8).writeFloat(0);assert.throws(()=>applyComparisonView(null,view,{}),/Invalid comparison view viewport/);
'''.replace('__FUNCTIONS__',functions)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    def test_host_native_pacing_is_only_enabled_by_the_private_comparison_flag(self):
        hook=SCRIPT[SCRIPT.index('if(!cfg.renderOnly && (cfg.measureRendering'):SCRIPT.index('let damageFixture=')]
        code=r'''
const assert=require('assert');
function run(comparisonFrameLimit,renderOnly=false){
 const cfg={renderOnly,measureRendering:true,windowTitle:'owned private test'},comparisonViewConfig={ident:0x70000002,zoom:2};
 let nativeDrawThread=null,nativeSwapThread=null,drawCalls=0,displayFrames=0,lastPresentation=0,longFrames=0,maxFrameMs=0,paceCalls=0,paceBindings=0;
 const frameHistogram=new Array(251).fill(0),hooks=new Map();
 const Interceptor={attach:(address,callbacks)=>hooks.set(address,callbacks)};
 const Process={getCurrentThreadId:()=>1,getModuleByName:()=>({getExportByName:name=>name})};
 const NativeFunction=function(name){if(name==='RepopulatedPaceFrame'){paceBindings++;return ()=>paceCalls++;}return ()=>{};};
 const Memory={allocUtf8String:value=>value},game={getExportByName:()=> 'draw'};
 const capturePresentationStage=()=>{},applyComparisonView=(zone,view)=>view,tracePresentation=()=>{};
 __HOOK__
 if(renderOnly){assert.strictEqual(hooks.size,0);return;}
 hooks.get('draw').onEnter.call({},[{},{}]);
 for(let frame=0;frame<3;frame++)hooks.get('SDL_GL_SwapWindow').onEnter([{}]);
 assert.strictEqual(displayFrames,3);assert.strictEqual(paceBindings,comparisonFrameLimit?1:0);
 assert.strictEqual(paceCalls,comparisonFrameLimit?3:0);
}
run(false);run(true);run(true,true);
'''.replace('__HOOK__',hook)
        checked=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)


if __name__=='__main__':unittest.main()
