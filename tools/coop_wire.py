"""Bounded protocol for the native cooperative prototype; separate from release transport."""
import base64
import hashlib
import math
from scene_codec import geometry
from presentation_wire import validate_visuals
from campaign_map_wire import validate_map

GAME_HASH='8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c'
PILOT=0x70000002
WIRE_LIMIT=4*1024*1024
NATIVE_VERSION=7


def snapshot(data,seq,roots,with_pose=False,visuals=None,campaign_map=None):
    if type(seq) is not int or not 0<seq<=10000000:raise ValueError('Invalid snapshot sequence')
    if type(roots) is not int or not 0<=roots<=4096:raise ValueError('Invalid root count')
    ships=geometry(data)
    if len(ships)!=roots or PILOT not in ships:raise ValueError('Missing pilot or invalid scene')
    if ships[PILOT]['faction']!=20008:raise ValueError('Pilot ownership differs')
    message={'type':'scene','seq':seq,'roots':roots,'sha256':hashlib.sha256(data).hexdigest(),
             'payload':base64.b64encode(data).decode()}
    if visuals is not None:message['visuals']=validate_visuals(visuals,ships)
    if campaign_map is not None:message['map']=validate_map(campaign_map)
    return (message,ships[PILOT]['pose']) if with_pose else message


def decode_snapshot(message,previous,read_scene=None):
    required={'type','seq','roots','sha256','payload'}
    if not isinstance(message,dict) or not required<=set(message)<=required|{'visuals','map'} or message['type']!='scene':
        raise ValueError('Invalid scene envelope')
    if type(message['seq']) is not int or not previous<message['seq']<=10000000:raise ValueError('Stale scene')
    if not isinstance(message['payload'],str) or len(message['payload'])>2800000:raise ValueError('Invalid scene payload')
    data=base64.b64decode(message['payload'],validate=True)
    if len(data)>2*1024*1024 or hashlib.sha256(data).hexdigest()!=message['sha256']:raise ValueError('Scene hash mismatch')
    parsed=read_scene(data) if read_scene is not None else None
    ships=geometry(data) if parsed is None else parsed[1]
    if type(message['roots']) is not int or len(ships)!=message['roots'] or PILOT not in ships:
        raise ValueError('Scene roots or pilot differ')
    if ships[PILOT]['faction']!=20008:raise ValueError('Pilot ownership differs')
    if 'visuals' in message:validate_visuals(message['visuals'],ships)
    if 'map' in message:validate_map(message['map'])
    return (data,ships[PILOT]['pose'],parsed) if read_scene is not None else (data,ships[PILOT]['pose'])


def coalesce_inputs(previous,current):
    """Keep latest movement/aim and native firing presses awaiting transport.

    Callers supply already validated states. A removed weapon's identifier
    never transfers its request to a new weapon after damage or respawn.
    """
    if len(previous)!=1 or len(current)!=1 or previous[0].get('action')!='native' or current[0].get('action')!='native':return current
    old={row[0]:row[1] for row in previous[0]['weapons']}
    result=dict(current[0]);result['weapons']=[list(row) for row in current[0]['weapons']]
    for row in result['weapons']:row[1]|=old.get(row[0],0)
    return [result]


def control(message,previous):
    if isinstance(message,dict) and message.get('action')=='native':
        if set(message)!={'type','seq','action','dimensions','destination','precision','weapons','viewRadius','clientTick'} or message['type']!='input':
            raise ValueError('Invalid native control envelope')
        seq=message['seq'];dimensions=message['dimensions']
        if type(seq) is not int or not previous<seq<=100000000:raise ValueError('Stale input')
        if type(message['clientTick']) is not int or not 0<=message['clientTick']<=100000000:raise ValueError('Invalid client prediction tick')
        if type(dimensions) is not int or dimensions<0 or dimensions&~0x1df:raise ValueError('Invalid navigation dimensions')
        if type(message['viewRadius']) not in (int,float) or not math.isfinite(message['viewRadius']) or not 1000<=message['viewRadius']<=20000:
            raise ValueError('Invalid client view radius')
        for name,size,limits in (('destination',6,(1000000,1000000,10000,10000,100,10000)),('precision',4,(10000,10000,100,100))):
            values=message[name]
            if type(values) is not list or len(values)!=size or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v,limit in zip(values,limits)):
                raise ValueError('Invalid native navigation values')
            if name=='precision' and any(v<0 for v in values):raise ValueError('Negative navigation precision')
        weapons=message['weapons'];seen=set()
        if type(weapons) is not list or len(weapons)>256:raise ValueError('Native weapon limit')
        for row in weapons:
            if type(row) is not list or len(row)!=8:raise ValueError('Invalid native weapon shape')
            bid,mask,*target=row
            if type(bid) is not int or not 1<=bid<=0xffffffff or bid in seen or type(mask) is not int or mask<0 or mask&~0x800008e0:
                raise ValueError('Invalid native weapon identity/mask')
            seen.add(bid)
            if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v,limit in zip(target,(1000000,1000000,10000,10000,100,100))):
                raise ValueError('Invalid native weapon target')
        return dict(message,ownerFaction=20008,targetFaction=20008)
    if not isinstance(message,dict) or set(message) not in ({'type','seq','action','x','y'}, {'type','seq','action','x','y','angle'}, {'type','seq','action','x','y','held'}) or message['type']!='input':
        raise ValueError('Invalid control envelope')
    seq=message['seq']
    if type(seq) is not int or not previous<seq<=100000000:raise ValueError('Stale input')
    action=message['action']
    if action not in ('drive','fire'):raise ValueError('Invalid action')
    limit=1 if action=='drive' else 1000000
    if any(type(message[a]) not in (int,float) or not math.isfinite(message[a]) or abs(message[a])>limit for a in ('x','y')):
        raise ValueError('Invalid control coordinates')
    result={'seq':seq,'action':action,'x':message['x'],'y':message['y'],'ownerFaction':20008,'targetFaction':20008}
    if 'held' in message:
        if action!='fire' or type(message['held']) is not bool:raise ValueError('Invalid fire button state')
        result['held']=message['held']
    if 'angle' in message:
        angle=message['angle']
        if action!='drive' or type(angle) not in (int,float) or not math.isfinite(angle) or abs(angle)>math.pi:
            raise ValueError('Invalid aiming angle')
        result['angle']=angle
    return result
