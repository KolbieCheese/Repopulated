from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from native_checkpoints import Checkpoints


class CheckpointTests(unittest.TestCase):
    def test_transient_windows_lock_keeps_publish_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            for name in ('save.lua','blueprints.lua','campaign-map.json'):(source/name).write_bytes(b'fixture')
            store=Checkpoints(root/'store');rename=Path.rename;attempts=[]
            def locked_once(path,destination):
                attempts.append(path)
                self.assertEqual(store.list(),[])
                if len(attempts)==1:raise PermissionError('Test sharing violation')
                return rename(path,destination)
            with patch.object(Path,'rename',locked_once),patch('native_checkpoints.time.sleep'):
                manifest=store.publish(source,'a'*64,'test')
            self.assertEqual(len(attempts),2)
            self.assertEqual(store.load(manifest['id'],'a'*64)[1],manifest)

    def test_roundtrip_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            for name in ('save.lua','blueprints.lua','cluster0x0_16x16.dat'):(source/name).write_bytes(b'fixture')
            store=Checkpoints(root/'store');manifest=store.publish(source,'a'*64,'test')
            path,loaded=store.load(manifest['id'],'a'*64)
            self.assertEqual(loaded,manifest);self.assertEqual(len(store.list()),1)
            with self.assertRaises(ValueError):store.load(manifest['id'],'b'*64)
            (path/'save.lua').write_bytes(b'changed')
            with self.assertRaises(ValueError):store.load(manifest['id'],'a'*64)

    def test_ownership_and_partial_generation_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            store=Checkpoints(root/'store')
            with self.assertRaises(ValueError):store.publish(source,'a'*64,'test')
            self.assertEqual(store.list(),[])
            for name in ('save.lua','blueprints.lua','map1.lua'):(source/name).write_bytes(b'fixture')
            saved=store.publish(source,'a'*64,'test');path=store.root/saved['id']/'manifest.json'
            saved['seats'][1]['faction']=100;path.write_text(json.dumps(saved))
            with self.assertRaises(ValueError):store.load(saved['id'],'a'*64)
            with self.assertRaises(ValueError):store.load('../escape','a'*64)


if __name__=='__main__':unittest.main()
