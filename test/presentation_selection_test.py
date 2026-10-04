from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_native_probe import SCRIPT


@unittest.skipUnless(shutil.which('node'),'Node is required to test native presentation selection')
class PresentationSelectionTests(unittest.TestCase):
    def test_rejected_motion_retains_exact_frame_and_remains_fatal(self):
        function=SCRIPT[SCRIPT.index('function applyMotionFrame('):SCRIPT.index('let interestExporter=')]
        code=r'''
const assert=require('assert');
let lastMotionApplied=0,motionApplied=0,replicaPresentedAt=0;
const timing=()=>{},nativeStage=()=>{},motionBegin=()=>{},predictionAck=null;
const messages=[],send=message=>messages.push(message),hexBuffer=()=> 'exact-rejected-payload';
const motionApply=()=>-4,motionApplyMessage=()=>({readUtf8String:()=> 'identity=1879048254 sourceFaction=0 liveFaction=8'});
const worldSeq=221,lastVisualFrame={seq:1000},streamStats={maxApplyMs:0};
__FUNCTION__
const frame={seq:1003,count:47,buffer:null,received:Date.now(),sourceTimeMs:18474289,simTimeMs:54283,inputTick:0};
for(const replay of [true,false]){
 assert.throws(()=>applyMotionFrame(null,frame,replay),/Native motion apply failed: -4; seq=1003 replay=/);
 const record=messages[messages.length-1];
 assert.strictEqual(record.type,'motion-apply-failed');assert.strictEqual(record.replay,replay);
 assert.strictEqual(record.seq,1003);assert.strictEqual(record.poses,'exact-rejected-payload');
 assert.strictEqual(record.worldSeq,221);assert.strictEqual(record.visualFrameSeq,1000);
}
assert.strictEqual(motionApplied,0);assert.strictEqual(replicaPresentedAt,0);
'''.replace('__FUNCTION__',function)
        result=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_visual_health_movers_share_source_selection_and_busy_clock_does_not_rewind(self):
        functions=SCRIPT[SCRIPT.index('function retainVisualFrame('):SCRIPT.index('function applyMotionFrame(')]
        code=r'''
const assert=require('assert');
function run(presentationDelayMs){
 const cfg={};let realtimeMoverWindowApply=null,lastMoverWindow=null;
 const frame=n=>({seq:n,sourceTimeMs:n*100,visualEpoch:0,health:{buffer:'h'+n,count:1},blocks:{buffer:'b'+n,count:1},
                  projectiles:{buffer:'p'+n,count:1},movers:{buffer:'m'+n,count:1}});
 const preparedVisualFrames=[1,2,3,4].map(frame);let lastMotion=preparedVisualFrames[2],lastVisualFrame=null,lastHealthFrame=null,visualFrameApplications=0,view=245;
 let visualHistoryEpoch=0,visualHistoryResets=0;
 const calls=[],viewSourceTimeReader=()=>view,visualBegin=t=>calls.push(['source',t]);
 const realtimeHealthApply=(z,b)=>{calls.push(['health',b]);return 1;};
 const realtimeVisualApply=(z,b,n,p)=>{calls.push(['visual',b,p]);return 1;};
 const realtimeMoversApply=b=>{calls.push(['mover',b]);return 1;};
 const nativeStage=()=>{},timing=()=>{},ptr=n=>n;
 __FUNCTIONS__
 assert.strictEqual(selectVisualFrame(preparedVisualFrames,50).seq,1);
 assert.strictEqual(selectVisualFrame(preparedVisualFrames,200).seq,2);
 assert.strictEqual(currentVisualFrame().seq,presentationDelayMs?2:3);
 assert.strictEqual(calls.length,0);assert.strictEqual(lastVisualFrame,null);assert.strictEqual(lastHealthFrame,null);
 consumeVisualFrame(null);
 const selected=presentationDelayMs?2:3;
 assert.strictEqual(lastVisualFrame.seq,selected);assert.strictEqual(lastHealthFrame.seq,selected);
 assert.deepStrictEqual(calls.slice(-3),[['health','h'+selected],['visual','b'+selected,'p'+selected],['mover','m'+selected]]);
 const previous=calls.length;view=-1;consumeVisualFrame(null);assert.strictEqual(calls.length,previous);
 assert.strictEqual(currentVisualFrame().seq,selected);assert.strictEqual(calls.length,previous);
 assert.strictEqual(lastVisualFrame.seq,selected);assert.strictEqual(lastHealthFrame.seq,selected);
 view=500;consumeVisualFrame(null);assert.strictEqual(lastVisualFrame.seq,3); // Future frame4 has no accepted pose yet.
 assert.strictEqual(lastHealthFrame.seq,3);
 const beforeRestore=calls.length;consumeVisualFrame(null,true);
 assert.deepStrictEqual(calls.slice(beforeRestore),[['source',300],['health','h3']]);
 assert.strictEqual(visualFrameApplications,presentationDelayMs?2:1);
 const history=[];
 for(let n=1;n<=15;n++)retainVisualFrame(history,frame(n));
 assert.strictEqual(history.length,12);assert.strictEqual(history[0].seq,4);
 const atBoundary=frame(16);atBoundary.sourceTimeMs=2000;retainVisualFrame(history,atBoundary);
 assert.strictEqual(history.length,12);assert.strictEqual(visualHistoryResets,0); // Exactly500ms retains history.
 const afterGap=frame(17);afterGap.sourceTimeMs=2501;retainVisualFrame(history,afterGap);
 assert.strictEqual(history.length,1);assert.strictEqual(visualHistoryResets,1);
 assert.strictEqual(afterGap.visualEpoch,1);
 preparedVisualFrames.splice(0,preparedVisualFrames.length,afterGap);lastMotion=afterGap;view=-1;
 consumeVisualFrame(null);assert.strictEqual(lastVisualFrame.seq,17);assert.strictEqual(lastHealthFrame.seq,17);
 assert.deepStrictEqual(calls.slice(-3),[['health','h17'],['visual','b17','p17'],['mover','m17']]);
}
run(0);run(100);
'''.replace('__FUNCTIONS__',functions)
        result=subprocess.run([shutil.which('node'),'-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
