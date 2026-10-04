"""Incremental, data-only scene plans. Commit only after native acknowledgement."""
import json
import math
import struct
from scene_codec import Reader, geometry_clusters

POSE=struct.Struct('<IIffffffff')
HEALTH=struct.Struct('<Ifff')
HEADER=b'offset={0,0}\nradius={9000,9000}\nviewpos={3000,3000,1000}\n'


def command_generation(entry,ident):
    """A ship shape can change while its persistent command block survives."""
    commands=[block for block in entry[0]['fields']['blocks']['items']
              if block['fields'].get('command',{}).get('fields',{}).get('ident')==ident]
    if len(commands)!=1:return None
    block=commands[0]['fields'];bid=block.get('persistentIdent',0)
    faction=block['command']['fields'].get('faction',0)
    if any(type(value) not in (int,float) or not math.isfinite(value) or int(value)!=value for value in (bid,faction)):
        return None
    if not 1<=bid<=0xffffffff or not 0<=faction<=0x7fffffff:return None
    return [int(bid),int(faction)]


def commandless_generation(entry,ident):
    """Anchor neutral root continuity to its surviving minimum top-block ID."""
    if type(ident) is not int or not 1<=ident<=0xffffffff:return None
    blocks=entry[0]['fields']['blocks']['items']
    if not 1<=len(blocks)<=4096:return None
    ids=[]
    for block in blocks:
        fields=block['fields']
        # A serialized command, even one without an actor ID, is insufficient
        # evidence for a commandless lifetime. The native guard also checks
        # actual COMMAND feature bits and fresh top-block ownership.
        if 'command' in fields:return None
        bid=fields.get('persistentIdent',0)
        if type(bid) not in (int,float) or not math.isfinite(bid) or int(bid)!=bid or not 1<=bid<=0xffffffff:return None
        ids.append(int(bid))
    if len(set(ids))!=len(ids) or min(ids)!=ident:return None
    return [ident,0,2]


def transition_details(ident,before,after):
    """Compact test evidence; exclude the runtime values ignored by fingerprint."""
    old=json.loads(before[2])['fields'] if before else {}
    new=json.loads(after[2])['fields']
    old_blocks={int(block['fields']['persistentIdent']):block for block in old.get('blocks',{}).get('items',[])}
    new_blocks={int(block['fields']['persistentIdent']):block for block in new['blocks']['items']}
    old_command=command_generation(before,ident) if before else None
    new_command=command_generation(after,ident)
    old_fragment=commandless_generation(before,ident) if before else None
    new_fragment=commandless_generation(after,ident)
    def command_fields(fields):
        for block in fields.get('blocks',{}).get('items',[]):
            command=block['fields'].get('command',{}).get('fields',{})
            if command.get('ident')==ident:return command
        return {}
    old_fields,new_fields=command_fields(old),command_fields(new)
    return {'ident':ident,'beforeCommand':old_command,'afterCommand':new_command,
            'beforeFragment':old_fragment,'afterFragment':new_fragment,
            'sameLifetime':old_command is not None and old_command==new_command or old_fragment is not None and old_fragment==new_fragment,
            'addedBlocks':len(new_blocks.keys()-old_blocks.keys()),
            'removedBlocks':len(old_blocks.keys()-new_blocks.keys()),
            'changedBlocks':sum(old_blocks[bid]!=new_blocks[bid] for bid in old_blocks.keys() & new_blocks.keys()),
            'rootFields':[key for key in sorted(old.keys()|new.keys()) if key!='blocks' and old.get(key)!=new.get(key)],
            'commandFields':[key for key in sorted(old_fields.keys()|new_fields.keys()) if old_fields.get(key)!=new_fields.get(key)]}


def fingerprint(cluster):
    def normalize(node,root=False):
        # Attached missiles and rotating subclusters have the same runtime
        # fields as roots. Their timers must not rebuild their entire parent.
        fields=dict(node['fields'])
        if root:
            for key in ('position','velocity','angle'):fields.pop(key,None)
        blocks=[]
        for block in node['fields']['blocks']['items']:
            values={key:value for key,value in block['fields'].items() if key not in ('health','lifetime','growFrac')}
            if root and 'command' in values:
                command=values['command'];values['command']=dict(command,fields={key:value for key,value in command['fields'].items() if key not in ('energy','resources','resourceCapacity')})
            blocks.append(dict(block,fields=values))
        # Runtime frames require unique persistent IDs. Sorting by those IDs
        # avoids repeatedly encoding embedded command blueprints as sort keys.
        ids=[b['fields'].get('persistentIdent',0) for b in blocks]
        key=(lambda b:b['fields']['persistentIdent']) if all(type(bid) in (int,float) and bid>0 for bid in ids) and len(set(ids))==len(ids) else (lambda b:json.dumps(b,sort_keys=True))
        fields['blocks']=dict(node['fields']['blocks'],items=sorted(blocks,key=key))
        if 'subclusters' in fields:fields['subclusters']=dict(fields['subclusters'],items=[normalize(child) for child in fields['subclusters']['items']])
        return dict(node,fields=fields)
    return json.dumps(normalize(cluster,True),sort_keys=True,separators=(',',':'))


class ReplicaPlan:
    def __init__(self,initial):
        self.current=self.read(initial)

    @staticmethod
    def read(data):
        return ReplicaPlan.read_with_shapes(data)[0]

    @staticmethod
    def read_with_shapes(data):
        reader=Reader(data);values=[]
        while reader.peek() is not None:
            name=reader.take()
            if name=='cluster':
                start=reader.index;value=reader.value()
                values.append((value,'cluster '+' '.join(reader.tokens[start:reader.index])))
            else:
                reader.take('=');reader.value()
        shapes=geometry_clusters([value for value,text in values])
        return {ident:(value,text,fingerprint(value)) for ident,(value,text) in zip(shapes,values)},shapes

    def prepare(self,data,parsed=None,diagnostics=False):
        next_state,shapes=self.read_with_shapes(data) if parsed is None else parsed
        replacements={ident for ident,entry in next_state.items()
                      if ident not in self.current or entry[2]!=self.current[ident][2]}
        removed=sorted((self.current.keys()-next_state.keys()) | (replacements & self.current.keys()))
        continuity=[]
        for ident in sorted(replacements & self.current.keys()):
            previous=command_generation(self.current[ident],ident)
            if previous is not None and previous==command_generation(next_state[ident],ident):
                continuity.append([ident,*previous])
            elif previous is None:
                fragment=commandless_generation(self.current[ident],ident)
                if fragment is not None and fragment==commandless_generation(next_state[ident],ident):
                    continuity.append([ident,*fragment])
        additions=HEADER+'\n'.join(next_state[ident][1] for ident in sorted(replacements)).encode()
        poses=[];health=[];seen=set()
        for ident,shape in shapes.items():
            pose=shape['pose']
            if len(pose)!=5 or any(not math.isfinite(n) or abs(n)>1000000 for n in pose):
                raise ValueError('Invalid incremental pose')
            if any(abs(n)>10000 for n in pose[2:]):raise ValueError('Invalid incremental velocity/angle')
            command=next((b['fields']['command']['fields'] for b in next_state[ident][0]['fields']['blocks']['items']
                          if 'command' in b['fields'] and b['fields']['command']['fields'].get('ident')==ident),{})
            state=[command.get(key,0) for key in ('energy','resources','resourceCapacity')]
            if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e9 for v in state):raise ValueError('Invalid command state')
            poses.append(POSE.pack(ident,shape['faction'],*pose,*state))
            def cluster_blocks(node):
                yield from node['fields']['blocks']['items']
                for child in node['fields'].get('subclusters',{}).get('items',[]):
                    yield from cluster_blocks(child)
            for block in cluster_blocks(next_state[ident][0]):
                bid=block['fields'].get('persistentIdent',0)
                if type(bid) not in (int,float) or not math.isfinite(bid) or not 1<=bid<=0xffffffff or int(bid)!=bid:
                    raise ValueError('Missing or invalid persistent block ID')
                bid=int(bid)
                if bid in seen:raise ValueError('Duplicate persistent block ID')
                seen.add(bid)
                value=block['fields'].get('health',-1)
                if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e9:
                    raise ValueError('Invalid incremental block health')
                # Native cleanup can serialize a terminal block before removing
                # it. Display zero health, rather than mistaking -1 for repaired.
                if 'health' in block['fields']:value=max(0,value)
                grow=block['fields'].get('growFrac',-1);lifetime=block['fields'].get('lifetime',-1)
                if type(grow) not in (int,float) or not math.isfinite(grow) or grow!=-1 and not 0<=grow<=1:
                    raise ValueError('Invalid replica growth')
                if type(lifetime) not in (int,float) or not math.isfinite(lifetime) or not -1<=lifetime<=1e6:
                    raise ValueError('Invalid replica lifetime')
                health.append((bid,value,grow,lifetime))
        if len(health)>65536:raise ValueError('Incremental block limit')
        plan={'state':next_state,'additions':additions if replacements else None,'remove':removed,'continuity':continuity,
                'poses':b''.join(poses).hex(),'health':b''.join(HEALTH.pack(*row) for row in sorted(health)).hex(),
                'retained':len(next_state)-len(replacements),'replaced':len(replacements)}
        if diagnostics:
            plan['transitions']=[transition_details(ident,self.current.get(ident),next_state[ident]) for ident in sorted(replacements)]
        return plan

    def commit(self,plan):
        self.current=plan['state']
