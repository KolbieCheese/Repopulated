"""Audit serialized state fidelity separately from geometric reconstruction.

No defaults are inferred: an omitted field may mean an engine default rather
than zero. Differences remain evidence, even where geometry still matches.
"""
from scene_codec import Reader, geometry


def entities(data):
    identities = list(geometry(data))
    return dict(zip(identities, Reader(data).scene()))


def normalize(value):
    if not isinstance(value, dict):
        return value
    fields = {key: normalize(item) for key, item in value['fields'].items()}
    items = [normalize(item) for item in value['items']]
    if 'blocks' in fields:
        # Match native vector order independently of block geometry. Ambiguous
        # colocated blocks remain positional; do not discard duplicate entries.
        blocks = fields['blocks']['items']
        def shape(block):
            raw = block['items']
            offset = raw[1]['items'] if len(raw) > 1 else [0, 0]
            return (raw[0], *offset, raw[2] if len(raw) > 2 else 0)
        blocks.sort(key=shape)
    return {'fields': fields, 'items': items}


def audit_fidelity(before, after, limit=256):
    if not isinstance(limit, int) or not 1 <= limit <= 4096:
        raise ValueError('Invalid difference limit')
    left, right = entities(before), entities(after)
    differences = []
    total = 0
    missing = object()

    def record(path, a, b):
        nonlocal total
        total += 1
        if len(differences) < limit:
            def summary(item):
                if item is missing:
                    return {'present': False}
                if isinstance(item, dict):
                    return {'present': True, 'type': 'table',
                            'fields': sorted(item['fields']), 'items': len(item['items'])}
                return {'present': True, 'value': item}
            differences.append({'path': path, 'host': summary(a), 'replica': summary(b)})

    def walk(a, b, path):
        if a is missing or b is missing:
            record(path, a, b)
        elif isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a['fields'].keys() | b['fields'].keys()):
                walk(a['fields'].get(key, missing), b['fields'].get(key, missing), path + '.' + key)
            for index in range(max(len(a['items']), len(b['items']))):
                walk(a['items'][index] if index < len(a['items']) else missing,
                     b['items'][index] if index < len(b['items']) else missing, path + f'[{index}]')
        elif a != b:
            # Only native float printing noise is tolerated. A health/lifetime
            # difference of one is never hidden by a relative tolerance.
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a-b) <= 0.001000001:
                return
            record(path, a, b)

    for ident in sorted(left.keys() | right.keys()):
        walk(normalize(left[ident]) if ident in left else missing,
             normalize(right[ident]) if ident in right else missing, f'entity[{ident:#x}]')
    return {'serializedStateEquivalent': total == 0, 'differenceCount': total,
            'truncated': total > limit, 'differences': differences,
            'scope': 'Serialized fields only; engine fields omitted by serialization are not validated.'}
