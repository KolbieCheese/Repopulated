"""Small authoritative motion frames, independent of Lua geometry snapshots."""
import math
import re
import struct
from presentation_wire import BLOCK,PROJECTILE

KINEMATICS=struct.Struct('<IIfffffffff')
MOVER=struct.Struct('<IIff')
HEALTH=struct.Struct('<Ifff')


def rows(payload,layout,limit):
    if type(payload) is not str or len(payload)%(layout.size*2) or len(payload)>limit*layout.size*2 or (payload and not re.fullmatch('[a-f0-9]+',payload)):
        raise ValueError('Invalid realtime payload')
    return list(layout.iter_unpack(bytes.fromhex(payload)))


def validate_motion(message,previous=0,previous_time=0,previous_sim_time=-1):
    if type(message) is not dict or set(message)!={'type','seq','sourceTimeMs','simTimeMs','poses','inputSeq','inputTick','blocks','projectiles','movers','health'} or message['type']!='motion':
        raise ValueError('Invalid motion envelope')
    if type(message['seq']) is not int or not previous<message['seq']<=10000000:
        raise ValueError('Stale motion frame')
    # This is the source's monotonic uptime, not its wall clock. Clients use
    # differences between samples and estimate their local clock offset.
    if type(message['sourceTimeMs']) is not int or not previous_time<message['sourceTimeMs']<=1000000000000:
        raise ValueError('Invalid motion source time')
    if type(message['simTimeMs']) is not int or not max(0,previous_sim_time)<=message['simTimeMs']<=1000000000000:
        raise ValueError('Invalid motion simulation time')
    if type(message['inputSeq']) is not int or not -1<=message['inputSeq']<=100000000:
        raise ValueError('Invalid input acknowledgement')
    if type(message['inputTick']) is not int or not 0<=message['inputTick']<=100000000:
        raise ValueError('Invalid prediction acknowledgement')
    payload=message['poses']
    if type(payload) is not str or not payload or len(payload)%(KINEMATICS.size*2) or len(payload)>4096*KINEMATICS.size*2 or not re.fullmatch('[a-f0-9]+',payload):
        raise ValueError('Invalid motion payload')
    poses=rows(payload,KINEMATICS,4096);seen=set()
    for ident,faction,*values in poses:
        if not ident or ident in seen or faction>2147483647:raise ValueError('Invalid motion identity')
        seen.add(ident)
        if any(not math.isfinite(v) or abs(v)>limit for v,limit in zip(values,(1e6,1e6,10000,10000,10000,1e9,1e9,1e9,10000))) or any(v<0 for v in values[5:8]):
            raise ValueError('Invalid motion coordinate/state')
    block_ids=set()
    for ident,bid,kind,x,y,angle,mask,turret,firing,sx,sy,ex,ey,hitting in rows(message['blocks'],BLOCK,4096):
        if ident not in seen or not bid or bid in block_ids or not kind or mask not in (1,2,3) or hitting not in (0,1):raise ValueError('Invalid realtime weapon identity')
        block_ids.add(bid)
        if any(not math.isfinite(v) or abs(v)>1e6 for v in (x,y,sx,sy,ex,ey)) or any(not math.isfinite(v) or abs(v)>10000 for v in (angle,turret)) or not math.isfinite(firing) or not 0<=firing<=1:
            raise ValueError('Invalid realtime weapon state')
        if not mask&2 and (firing or sx or sy or ex or ey or hitting):raise ValueError('Invalid realtime laser state')
    for x,y,vx,vy,angle,size,ttl,color,health in rows(message['projectiles'],PROJECTILE,2048):
        if any(not math.isfinite(v) or abs(v)>limit for v,limit in zip((x,y,vx,vy,angle,size,ttl,health),(1e6,1e6,1e6,1e6,10000,10000,120,1e9))) or size<0 or ttl<0 or health<0:
            raise ValueError('Invalid realtime projectile')
    previous_bid=0
    for ident,bid,accel,angular in rows(message['movers'],MOVER,4096):
        if not ident or not previous_bid<bid or not math.isfinite(accel) or not 0<=accel<=1.001 or not math.isfinite(angular) or abs(angular)>1.001:
            raise ValueError('Invalid realtime mover')
        previous_bid=bid
    previous_bid=0
    for bid,health,growth,lifetime in rows(message['health'],HEALTH,65536):
        if not previous_bid<bid or any(not math.isfinite(v) or not low<=v<=high for v,low,high in ((health,0,1e9),(growth,0,1),(lifetime,-1,1e6))):raise ValueError('Invalid realtime health')
        previous_bid=bid
    return poses
