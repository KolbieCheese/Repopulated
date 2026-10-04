"""Atomic, content-checked native alpha checkpoint generations."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
import time
from coop_wire import GAME_HASH,PILOT

ID=re.compile(r'^[a-f0-9]{32}$')


class Checkpoints:
    def __init__(self,root):self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def inventory(slot):
        paths=list(slot.iterdir())
        if not 3<=len(paths)<=4096 or any(p.is_symlink() or not p.is_file() for p in paths):raise ValueError('Invalid checkpoint files')
        if sum(p.stat().st_size for p in paths)>256*1024*1024:raise ValueError('Checkpoint size limit')
        names={p.name for p in paths}
        if not any(n in names for n in ('save.lua','save.lua.gz')) or not any(n in names for n in ('blueprints.lua','blueprints.lua.gz')):
            raise ValueError('Missing native campaign data')
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

    def publish(self,source,content,name):
        if not isinstance(content,str) or not re.fullmatch(r'[a-f0-9]{64}',content):raise ValueError('Invalid content fingerprint')
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('Invalid checkpoint name')
        source=Path(source).resolve();expected=self.inventory(source)
        ident=uuid.uuid4().hex;stage=self.root/('.stage-'+ident)
        stage.mkdir()
        shutil.copytree(source,stage/'slot')
        if self.inventory(stage/'slot')!=expected:raise ValueError('Checkpoint copy differs')
        manifest={'version':1,'id':ident,'name':name,'created':datetime.now(timezone.utc).isoformat(),
                  'gameHash':GAME_HASH,'contentHash':content,'mods':[],
                  'seats':[{'faction':100,'ident':0x70000001},{'faction':20008,'ident':PILOT}],
                  'files':expected,'scope':'native-two-player-alpha'}
        with (stage/'manifest.json').open('w',encoding='utf-8') as output:
            json.dump(manifest,output,indent=2);output.flush()
            import os
            os.fsync(output.fileno())
        # Windows scanners can briefly hold a newly written file/directory.
        # Keep the publish atomic and preserve the stage if the lock persists.
        for attempt in range(10):
            try:stage.rename(self.root/ident);break
            except PermissionError:
                if attempt==9:raise
                time.sleep(0.1*(attempt+1))
        return manifest

    def load(self,ident,content):
        if not isinstance(ident,str) or not ID.fullmatch(ident):raise ValueError('Invalid checkpoint identity')
        folder=self.root/ident
        if folder.is_symlink() or (folder/'slot').is_symlink():raise ValueError('Checkpoint links are unsupported')
        path=folder/'manifest.json'
        if path.is_symlink() or path.stat().st_size>1024*1024:raise ValueError('Invalid checkpoint manifest')
        manifest=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(manifest,dict) or type(manifest.get('version')) is not int or manifest.get('version')!=1 or manifest.get('id')!=ident or manifest.get('scope')!='native-two-player-alpha':
            raise ValueError('Unsupported checkpoint')
        if manifest.get('gameHash')!=GAME_HASH or manifest.get('contentHash')!=content or manifest.get('mods')!=[]:
            raise ValueError('Checkpoint executable or content mismatch')
        if manifest.get('seats')!=[{'faction':100,'ident':0x70000001},{'faction':20008,'ident':PILOT}]:raise ValueError('Checkpoint ownership differs')
        if self.inventory(folder/'slot')!=manifest.get('files'):raise ValueError('Checkpoint files differ')
        return folder/'slot',manifest

    def list(self):
        entries=[]
        for path in self.root.iterdir():
            if not ID.fullmatch(path.name) or not path.is_dir() or path.is_symlink():continue
            try:
                manifest=path/'manifest.json'
                if manifest.is_symlink() or manifest.stat().st_size>1024*1024:continue
                data=json.loads(manifest.read_text(encoding='utf-8'))
                if isinstance(data,dict) and data.get('id')==path.name:
                    entries.append({k:data.get(k) for k in ('id','name','created')})
            except (OSError,ValueError):continue
        return sorted(entries,key=lambda e:str(e['created']),reverse=True)
