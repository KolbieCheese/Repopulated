"""Bounded comparison of native runtime health readbacks."""
import math


def health_entities(records):
    if not isinstance(records,list) or len(records)>4096:
        raise ValueError('Invalid native health scene')
    result={}; total=0
    for record in records:
        ident=record.get('ident'); values=record.get('values')
        if type(ident) is not int or not 0<ident<=0xffffffff or ident in result:
            raise ValueError('Invalid native health identity')
        if not isinstance(values,list) or not 1<=len(values)<=4096:
            raise ValueError('Invalid native health vector')
        total+=len(values)
        if total>65536 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1e9 for v in values):
            raise ValueError('Invalid native block health')
        result[ident]=sorted(values)
    return result


def audit_health(host,replica):
    left,right=health_entities(host),health_entities(replica)
    if left.keys()!=right.keys():
        raise ValueError('Runtime health entity IDs differ')
    mismatches=[]; maximum=0; count=0
    for ident,values in left.items():
        if len(values)!=len(right[ident]):
            raise ValueError('Runtime health block counts differ')
        for index,(a,b) in enumerate(zip(values,right[ident])):
            error=abs(a-b); maximum=max(maximum,error); count+=1
            if error>0.02 and len(mismatches)<256:
                mismatches.append({'ident':ident,'sortedIndex':index,'host':a,'replica':b})
    return {'nativeHealthMultisetsEquivalent':maximum<=0.02,'blocksCompared':count,
            'maximumHealthError':maximum,'mismatches':mismatches,
            'scope':'Per-entity sorted health values; individual block identity and other transient fields are not validated.'}
