"""Read-only parser for Reassembly data tables; never executes Lua."""
import re

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}(),=]|[^\s{}(),=]+')


def read_data(path, stack=()):
    path = path.resolve()
    if path in stack:
        raise ValueError(f'Recursive include: {path}')
    text = path.read_text(encoding='utf-8-sig')
    # Strip comments while preserving strings; process includes before tokenizing.
    text = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|--[^\n]*|//[^\n]*',
                  lambda m: '' if m[0].startswith(('--', '//')) else m[0], text)
    def include(m):
        included = (path.parent / m[1]).resolve()
        if not included.is_relative_to(path.parent):
            raise ValueError(f'Include outside source directory: {included}')
        return read_data(included, stack + (path,))
    text = re.sub(r'^\s*#include\s+"([^"]+)"\s*$', include, text, flags=re.M)
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|#[^\n]*',
                  lambda m: '' if m[0].startswith('#') else m[0], text)


def parse(text):
    tokens = TOKEN.findall(text)
    i = 0
    def table():
        nonlocal i
        if tokens[i] != '{':
            raise ValueError('Expected data table')
        i += 1
        result = []
        while i < len(tokens) and tokens[i] != '}':
            if tokens[i] == ',':
                i += 1
                continue
            key = None
            if i + 1 < len(tokens) and tokens[i + 1] == '=':
                key = tokens[i]
                i += 2
            if tokens[i] == '{':
                value = table()
            else:
                parts, parens = [], 0
                while i < len(tokens):
                    token = tokens[i]
                    if parens == 0 and token in (',', '}'):
                        break
                    if parens == 0 and parts and i + 1 < len(tokens) and tokens[i + 1] == '=':
                        break
                    if token == '(':
                        parens += 1
                    elif token == ')':
                        parens -= 1
                    parts.append(token)
                    i += 1
                value = ''.join(parts)
            result.append((key, value))
        if i >= len(tokens):
            raise ValueError('Unclosed table')
        i += 1
        return result
    result = table()
    if i != len(tokens):
        raise ValueError(f'Unexpected trailing data at token {i}: {tokens[max(0,i-8):i+12]}')
    return result


def emit(table):
    return '{' + ', '.join((k + '=' if k is not None else '') +
                          (emit(v) if isinstance(v, list) else str(v)) for k, v in table) + '}'


def get(table, key, default=None):
    return next((v for k, v in reversed(table) if k == key), default)


def put(table, key, value):
    table[:] = [(k, v) for k, v in table if k != key]
    table.append((key, str(value)))


def number(value):
    return int(str(value), 0)


def records(path):
    return [v for k, v in parse(read_data(path)) if k is None and isinstance(v, list)]



