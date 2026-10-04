"""Data-only authoritative map schema and native packing; no executable Lua."""
import math
import struct


def validate_map(value):
    if type(value) is not dict or set(value)!={'version','exploration','radius','width','cells','regions','objectives'} or value['version']!=1 or type(value['version']) is not int or value['exploration'] not in ('shared','independent'):
        raise ValueError('Invalid campaign map envelope')
    width=value['width'];radius=value['radius'];cells=value['cells'];regions=value['regions']
    if type(width) is not int or not 1<=width<=256 or type(radius) is not list or len(radius)!=2 or any(type(v) not in (float,int) or not math.isfinite(v) or not 0<v<=1000000 for v in radius):
        raise ValueError('Invalid campaign map dimensions')
    if type(cells) is not list or len(cells)!=width*width or type(regions) is not list or len(regions)>1024:
        raise ValueError('Invalid campaign map size')
    ids=set()
    for row in regions:
        if type(row) is not list or len(row)!=3 or any(type(v) is not int for v in row) or not 1<=row[0]<=0x7fffffff or not 0<=row[1]<=0xffffffff or not 0<=row[2]<=1000000 or row[0] in ids:
            raise ValueError('Invalid campaign region')
        ids.add(row[0])
    used=set()
    for row in cells:
        if type(row) is not list or len(row)!=4 or type(row[0]) is not int or not 0<=row[0]<=0x7fffffff or type(row[1]) not in (float,int) or not math.isfinite(row[1]) or not 0<=row[1]<=1 or any(type(v) is not int or v not in (0,1) for v in row[2:]):
            raise ValueError('Invalid campaign cell')
        if row[0]:used.add(row[0])
    if used!=ids:raise ValueError('Campaign region coverage differs')
    objectives=value['objectives'];keys=set();idents=set()
    if type(objectives) is not list or len(objectives)>8192:raise ValueError('Campaign objective limit')
    for row in objectives:
        if type(row) is not list or len(row)!=9 or any(type(v) is not int for v in row[:4]) or not 1<=row[0]<0xffffffff or not 0<=row[1]<=0xffffffff or not 0<=row[2]<=1000000 or not 0<=row[3]<=0xffffffff or row[0] in keys or row[1] and row[1] in idents:
            raise ValueError('Invalid campaign objective identity')
        if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1000000 for v in row[4:]) or row[8]<0:
            raise ValueError('Invalid campaign objective position/radius')
        keys.add(row[0])
        if row[1]:idents.add(row[1])
    return value


def pack_map(value):
    validate_map(value)
    return {'width':value['width'],'radius':struct.pack('<ff',*value['radius']).hex(),
            'cells':b''.join(struct.pack('<ifBBBB',*row,0,0) for row in value['cells']).hex(),
            'regions':b''.join(struct.pack('<iIi',*row) for row in value['regions']).hex(),
            'objectives':b''.join(struct.pack('<IIiIfffff',*row) for row in value['objectives']).hex()}


def map_summary(value):
    validate_map(value)
    factions={row[0]:row[2] for row in value['regions']};explored=sum(row[2] for row in value['cells']);controlled={}
    for row in value['cells']:
        if row[2] and row[0]:
            faction=factions[row[0]];controlled[str(faction)]=controlled.get(str(faction),0)+1
    return {'cells':len(value['cells']),'explored':explored,'explorationPercent':round(100*explored/len(value['cells']),2),
            'visibleRegionCellsByFaction':controlled,'exploration':value['exploration'],'objectives':len(value['objectives']),
            'stations':sum(bool(row[3]&1) and not row[3]&0x20 for row in value['objectives'])}


def visible_map(value):
    """Keep authoritative terrain; send remote markers only in discovered cells."""
    validate_map(value)
    if value['exploration']=='shared':return value
    width=value['width'];rx,ry=value['radius']
    def explored(row):
        x=math.floor(row[4]*width/(2*rx))%width
        y=math.floor(row[5]*width/(2*ry))%width
        return bool(value['cells'][y*width+x][2])
    return dict(value,objectives=[row for row in value['objectives'] if explored(row)])


def load_map_settings(slot):
    path=slot/'map-settings.json'
    if not path.exists():return {'version':1,'sharedExploration':False}
    if path.stat().st_size>256:raise ValueError('Saved map settings size limit')
    import json
    value=json.loads(path.read_text())
    if type(value) is not dict or set(value)!={'version','sharedExploration'} or type(value['version']) is not int or value['version']!=1 or type(value['sharedExploration']) is not bool:
        raise ValueError('Invalid saved map settings')
    return value
