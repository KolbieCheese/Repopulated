"""Bounded reader for native text snapshots. Data only; never evaluates Lua."""
import json
import math
import re

TOKEN=re.compile(r'\s*("(?:\\.|[^"\\])*"|-?0x[\da-fA-F]+|-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_\d]*|[{},=|])')


class Reader:
    def __init__(self,data):
        if len(data)>2*1024*1024:
            raise ValueError('Snapshot size limit')
        text=data.decode('utf-8')
        self.tokens=[]
        position=0
        while position<len(text):
            match=TOKEN.match(text,position)
            if not match:
                if not text[position:].strip():
                    break
                raise ValueError('Unsupported snapshot token')
            self.tokens.append(match[1]); position=match.end()
            if len(self.tokens)>250000:
                raise ValueError('Snapshot token limit')
        self.index=0

    def peek(self,distance=0):
        index=self.index+distance
        return self.tokens[index] if index<len(self.tokens) else None

    def take(self,expected=None):
        token=self.peek()
        if token is None or expected is not None and token!=expected:
            raise ValueError('Malformed snapshot structure')
        self.index+=1
        return token

    def value(self,depth=0):
        if depth>32:
            raise ValueError('Snapshot nesting limit')
        token=self.take()
        if token=='{':
            fields={}; items=[]
            while self.peek()!='}':
                if self.peek(1)=='=':
                    name=self.take(); self.take('=')
                    if name in fields:
                        raise ValueError('Duplicate snapshot field')
                    fields[name]=self.value(depth+1)
                else:
                    items.append(self.value(depth+1))
                if self.peek()==',':
                    self.take(',')
                elif self.peek()!='}':
                    raise ValueError('Missing snapshot separator')
            self.take('}')
            return {'fields':fields,'items':items}
        if token.startswith('"'):
            return json.loads(token)
        if re.fullmatch(r'-?0x[\da-fA-F]+',token):
            return int(token,16)
        if re.fullmatch(r'-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',token):
            number=float(token)
            if not math.isfinite(number):
                raise ValueError('Nonfinite snapshot number')
            return number
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_\d]*',token):
            raise ValueError('Unexpected snapshot value')
        parts=[token]
        while self.peek()=='|':
            self.take('|'); parts.append(self.take())
        return '|'.join(parts)

    def scene(self):
        clusters=[]
        while self.peek() is not None:
            name=self.take()
            if name=='cluster':
                clusters.append(self.value())
            else:
                self.take('='); self.value()
        return clusters


def geometry(data):
    return geometry_clusters(Reader(data).scene())


def geometry_clusters(clusters):
    result={}
    for cluster in clusters:
        fields=cluster['fields']; blocks=fields['blocks']['items']
        commands=[b['fields']['command']['fields'] for b in blocks if 'command' in b['fields']
                  and b['fields']['command']['fields'].get('ident',0)]
        if len(commands)>1:
            raise ValueError('Cluster has ambiguous command IDs')
        # Native serialization can reorder a debris cluster's block vector.
        # Its tagged block need not be the first serialized block.
        fragment_ids=[int(b['fields']['persistentIdent']) for b in blocks if b['fields'].get('persistentIdent')]
        command=commands[0] if commands else {'ident':min(fragment_ids,default=0),'faction':0}
        ident=int(command['ident'])
        if not ident:
            raise ValueError('Cluster lacks persistent entity identity')
        if ident in result:
            raise ValueError('Duplicate scene identity')
        pose=fields.get('position',{'items':[0,0]})['items']
        velocity=fields.get('velocity',{'items':[0,0]})['items']
        shape=[]
        for block in blocks:
            items=block['items']
            offset=items[1]['items'] if len(items)>1 else [0,0]
            angle=items[2] if len(items)>2 else 0
            shape.append([int(items[0]),*offset,angle])
        result[ident]={'faction':int(command.get('faction',0)),'pose':[*pose,*velocity,fields.get('angle',0)],
                       'blocks':shape,'blockIds':[int(b['fields'].get('persistentIdent',0)) for b in blocks]}
    return result


def compare_geometry(before,after):
    expected,actual=geometry(before),geometry(after)
    if expected.keys()!=actual.keys():
        raise ValueError('Scene identities differ')
    for ident,left in expected.items():
        right=actual[ident]
        if left['faction']!=right['faction'] or len(left['blocks'])!=len(right['blocks']):
            raise ValueError('Faction or block count differs')
        if any(abs(a-b)>0.1 for a,b in zip(left['pose'],right['pose'])):
            raise ValueError('Scene pose differs')
        # Native reconstruction can reorder the cluster's block vector. Compare
        # geometry independently of that storage order.
        for a,b in zip(sorted(left['blocks']),sorted(right['blocks'])):
            if a[0]!=b[0] or any(abs(x-y)>0.01 for x,y in zip(a[1:],b[1:])):
                raise ValueError('Block topology differs')
    return len(expected)
