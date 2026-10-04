import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from record_native_pair import NativePairRecorder,OWNER


class NativeRecordingRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='repopulated-retention-')
        self.base=Path(self.temporary.name).resolve();self.root=self.base/'recordings';self.root.mkdir()
        self.recorder=object.__new__(NativePairRecorder);self.recorder.root=self.root

    def tearDown(self):self.temporary.cleanup()

    def recording(self,number,outcome='failed',finished=True,owner=OWNER):
        folder=self.root/str(number);folder.mkdir()
        metadata={'createdBy':owner,'testOutcome':outcome}
        if finished:metadata['finishedAt']=1
        (folder/'recording.json').write_text(json.dumps(metadata))
        (folder/'host.mp4').write_bytes(b'owned host recording')
        (folder/'client.mp4').write_bytes(b'owned client recording')
        return folder

    def test_failed_retries_preserve_latest_passed_comparison(self):
        passed=self.recording(100,'passed')
        discarded=[self.recording(200),self.recording(300)]
        latest=self.recording(400,'in-progress',finished=False)
        self.recorder.prune()
        self.assertTrue(passed.is_dir());self.assertTrue(latest.is_dir())
        self.assertTrue(all(not folder.exists() for folder in discarded))
        self.assertEqual({folder.name for folder in self.root.iterdir()},{'100','400'})

    def test_completed_success_prunes_without_starting_another_test(self):
        old=self.recording(100,'passed');self.recording(200,'passed')
        (old/'client-analysis-thrust.json').write_text('{}')
        (old/'client-analysis-native-acceptance.json').write_text('{}')
        (old/'client-analysis-thrust-native-sim.json').write_text('{}')
        (old/'client-analysis-thrust-mover-window.json').write_text('{}')
        latest=self.recording(300,'in-progress',finished=False)
        self.recorder.folder=latest;self.recorder.metadata=json.loads((latest/'recording.json').read_text())
        self.recorder.metadata['testOutcome']='passed'
        self.recorder.finished=False;self.recorder.started=False
        self.recorder.processes=[];self.recorder.logs=[]
        self.recorder.close()
        self.assertEqual({folder.name for folder in self.root.iterdir()},{'200','300'})
        self.assertIn('finishedAt',json.loads((latest/'recording.json').read_text()))

    def test_in_progress_unknown_files_and_foreign_owners_are_preserved(self):
        active=self.recording(100,'in-progress',finished=False)
        unknown=self.recording(200);(unknown/'user-notes.txt').write_text('keep my notes')
        foreign=self.recording(300,owner='another recorder')
        discarded=self.recording(400)
        passed=self.recording(500,'passed');latest=self.recording(600)
        self.recorder.prune()
        self.assertTrue(active.is_dir());self.assertTrue(unknown.is_dir());self.assertTrue(foreign.is_dir())
        self.assertEqual((unknown/'user-notes.txt').read_text(),'keep my notes')
        self.assertFalse(discarded.exists());self.assertTrue(passed.is_dir());self.assertTrue(latest.is_dir())

    def test_symlink_files_manifests_and_directories_are_never_removed(self):
        linked_file=self.recording(100);linked_manifest=self.recording(200)
        target=self.base/'outside.txt';target.write_text('outside content')
        target_manifest=self.base/'outside-recording.json'
        target_manifest.write_text(json.dumps({'createdBy':OWNER,'testOutcome':'failed','finishedAt':1}))
        target_folder=self.base/'outside-recording';target_folder.mkdir()
        (target_folder/'recording.json').write_text(target_manifest.read_text())
        (linked_file/'host.mp4').unlink();(linked_manifest/'recording.json').unlink()
        try:
            (linked_file/'host.mp4').symlink_to(target)
            (linked_manifest/'recording.json').symlink_to(target_manifest)
            (self.root/'300').symlink_to(target_folder,target_is_directory=True)
        except OSError as error:self.skipTest('Windows symlink creation unavailable: '+str(error))
        passed=self.recording(400,'passed');latest=self.recording(500)
        self.recorder.prune()
        self.assertTrue((linked_file/'host.mp4').is_symlink())
        self.assertTrue((linked_manifest/'recording.json').is_symlink())
        self.assertTrue((self.root/'300').is_symlink())
        self.assertEqual(target.read_text(),'outside content')
        self.assertTrue((target_folder/'recording.json').is_file())
        self.assertTrue(passed.is_dir());self.assertTrue(latest.is_dir())

    def test_symlink_guards_cover_all_three_entry_types_without_windows_privileges(self):
        linked_file=self.recording(100);linked_manifest=self.recording(200);linked_folder=self.recording(300)
        linked={linked_file/'host.mp4',linked_manifest/'recording.json',linked_folder}
        self.recording(400,'passed');self.recording(500)
        original=Path.is_symlink
        # Exercise every symlink guard even on Windows accounts that cannot
        # create filesystem links. The separate test above uses actual links.
        with patch.object(Path,'is_symlink',lambda path:path in linked or original(path)):
            self.recorder.prune()
        self.assertTrue(linked_file.is_dir());self.assertTrue(linked_manifest.is_dir());self.assertTrue(linked_folder.is_dir())


if __name__=='__main__':unittest.main()
