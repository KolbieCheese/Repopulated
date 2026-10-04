"""Validate native visual-only state before packing it for the exact-build DLL."""
import math
import struct

BLOCK=struct.Struct('<IIIfffIffffffI')
PROJECTILE=struct.Struct('<fffffffIf')
THRUST=struct.Struct('<fffffIfffIf')
MOTION=struct.Struct('<If')
PRESENTATION_VERSION=2


def number(value,limit,minimum=None):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>limit or minimum is not None and value<minimum:
        raise ValueError('Invalid presentation number')


def integer(value,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Invalid presentation integer')


def validate_visuals(visuals,ships):
    keys={'version','blocks','projectiles','thrust','droppedThrust'}
    if type(visuals) is not dict or type(visuals.get('version')) is not int or visuals['version'] not in (1,2) or set(visuals)!=(keys|{'motion'} if visuals['version']==2 else keys):
        raise ValueError('Invalid presentation envelope')
    if visuals['version']==2:
        rows=visuals['motion'];seen=set()
        if type(rows) is not list or len(rows)>4096:raise ValueError('Motion presentation limit')
        for row in rows:
            if type(row) is not list or len(row)!=2:raise ValueError('Invalid motion presentation shape')
            integer(row[0],1,0xffffffff);number(row[1],10000)
            if row[0] not in ships or row[0] in seen:raise ValueError('Invalid motion presentation identity')
            seen.add(row[0])
        if seen!=set(ships):raise ValueError('Incomplete motion presentation')
    integer(visuals['droppedThrust'],0,0xffffffff)
    for key,limit,length in (('blocks',4096,14),('projectiles',2048,9),('thrust',512,11)):
        rows=visuals[key]
        if type(rows) is not list or len(rows)>limit or any(type(row) is not list or len(row)!=length for row in rows):
            raise ValueError('Presentation row limit or shape')
    seen=set()
    for row in visuals['blocks']:
        ident,block_ident,type_id,x,y,angle,mask,turret,firing,sx,sy,ex,ey,hitting=row
        integer(ident,1,0xffffffff);integer(block_ident,1,0xffffffff);integer(type_id,1,0xffffffff);integer(mask,1,3);integer(hitting,0,1)
        if ident not in ships:raise ValueError('Presentation references absent entity')
        for value in (x,y,sx,sy,ex,ey):number(value,1000000)
        number(angle,100);number(turret,100);number(firing,1,0)
        key=(ident,block_ident)
        if key in seen:raise ValueError('Duplicate presentation block')
        seen.add(key)
        matches=[b for bid,b in zip(ships[ident]['blockIds'],ships[ident]['blocks']) if bid==block_ident and b[0]==type_id and all(abs(a-c)<0.01 for a,c in zip(b[1:],(x,y,angle)))]
        if len(matches)!=1:raise ValueError('Presentation block geometry differs')
        if not mask&2 and (firing or sx or sy or ex or ey or hitting):raise ValueError('Laser state on a non-laser record')
    for row in visuals['projectiles']:
        for value in row[:4]:number(value,1000000)
        number(row[4],100);number(row[5],10000,0);number(row[6],120,0);integer(row[7],0,0xffffffff);number(row[8],1e9,0)
    previous=-1
    for row in visuals['thrust']:
        for i in (0,1,2,3,6,7):number(row[i],1000000)
        for i in (4,8):number(row[i],10000,0)
        integer(row[5],0,0xffffffff);integer(row[9],0,0xffffffff);number(row[10],0.3,0)
        if row[10]<previous:raise ValueError('Unordered presentation events')
        previous=row[10]
    return visuals


def pack_visuals(visuals):
    return {key:b''.join(layout.pack(*row) for row in visuals.get(key,[])).hex()
            for key,layout in (('blocks',BLOCK),('projectiles',PROJECTILE),('thrust',THRUST),('motion',MOTION))}
