"""Incremental, data-only scene plans. Commit only after native acknowledgement."""
import copy
import json
import math
import struct
from scene_codec import Reader, geometry_clusters

POSE=struct.Struct('<IIffffffff')
HEALTH=struct.Struct('<Ifff')
HEADER=b'offset={0,0}\nradius={9000,9000}\nviewpos={3000,3000,1000}\n'


def fingerprint(cluster):
    value=copy.deepcopy(cluster)
    for key in ('position','velocity','angle'):
        value['fields'].pop(key,None)
    for block in value['fields']['blocks']['items']:
        block['fields'].pop('health',None)
        for key in ('lifetime','growFrac'):
            if key in block['fields']:block['fields'][key]='runtime'
        if 'command' in block['fields']:
            for key in ('energy','resources','resourceCapacity'):block['fields']['command']['fields'].pop(key,None)
    value['fields']['blocks']['items'].sort(key=lambda b:json.dumps(b,sort_keys=True))
    return json.dumps(value,sort_keys=True,separators=(',',':'))


class ReplicaPlan:
    def __init__(self,initial):
        self.current=self.read(initial)

    @staticmethod
    def read(data):
        reader=Reader(data);values=[]
        while reader.peek() is not None:
            name=reader.take()
            if name=='cluster':
                start=reader.index;value=reader.value()
                values.append((value,'cluster '+' '.join(reader.tokens[start:reader.index])))
            else:
                reader.take('=');reader.value()
        shapes=geometry_clusters([value for value,text in values])
        return {ident:(value,text,fingerprint(value)) for ident,(value,text) in zip(shapes,values)}

    def prepare(self,data):
        next_state=self.read(data);shapes=geometry_clusters([value[0] for value in next_state.values()])
        replacements={ident for ident,entry in next_state.items()
                      if ident not in self.current or entry[2]!=self.current[ident][2]}
        removed=sorted((self.current.keys()-next_state.keys()) | (replacements & self.current.keys()))
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
            for block in next_state[ident][0]['fields']['blocks']['items']:
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
        return {'state':next_state,'additions':additions if replacements else None,'remove':removed,
                'poses':b''.join(poses).hex(),'health':b''.join(HEALTH.pack(*row) for row in sorted(health)).hex(),
                'retained':len(next_state)-len(replacements),'replaced':len(replacements)}

    def commit(self,plan):
        self.current=plan['state']
