# -*- coding: utf-8 -*-
"""Keep "2. Compare and Update Redraw.dyn" in step with src/.

The Dynamo graph is the deliverable the operator opens, but the Python inside
it is too long to live in a JSON string and stay reviewable. The sources in
this folder are the truth; this script injects them into the graph without
touching anything else - the node layout, the connectors and the other inputs
are left exactly as they are.

    python3 src/build_graph2.py          # inject src/ into the graph
    python3 src/build_graph2.py --check  # fail if the graph is out of date

The pieces are concatenated in SOURCES order and share one namespace, so
merge_core.py is imported into the graph by being inlined rather than by an
import statement. A Dynamo Python node has no import path of its own.

The graph stays CRLF JSON with the Python stored as escaped \\r\\n strings,
like the other Dynamo files in the repository.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, '2. Compare and Update Redraw.dyn')

SOURCES = ['merge_core.py', 'graph2.py']

DEFINITION = re.compile(r'^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)


def read_sources():
    """The concatenated source, and the names each file defines."""
    chunks = []
    owners = {}
    for name in SOURCES:
        with open(os.path.join(HERE, name), 'r', encoding='utf-8') as handle:
            text = handle.read().replace('\r\n', '\n')
        for definition in DEFINITION.findall(text):
            owners.setdefault(definition, []).append(name)
        chunks.append('# ' + '-' * 70 + '\n# src/%s\n# ' % name + '-' * 70 + '\n'
                      + text.rstrip('\n') + '\n')
    clashes = sorted(name for name, files in owners.items() if len(files) > 1)
    if clashes:
        raise SystemExit('name clash between the sources: %s' % ', '.join(clashes))
    return '\n'.join(chunks)


def build():
    code = read_sources().replace('\n', '\r\n')
    with open(TARGET, 'r', encoding='utf-8', newline='') as handle:
        raw = handle.read()
    graph = json.loads(raw)
    nodes = [n for n in graph['Nodes'] if n.get('NodeType') == 'PythonScriptNode']
    if len(nodes) != 1:
        raise SystemExit('expected exactly one PythonScriptNode, found %d' % len(nodes))
    nodes[0]['Code'] = code
    return json.dumps(graph, indent=2, ensure_ascii=False).replace('\n', '\r\n')


def main():
    payload = build()
    if '--check' in sys.argv:
        with open(TARGET, 'r', encoding='utf-8', newline='') as handle:
            current = handle.read()
        if current != payload:
            print('OUT OF DATE: %s does not match src/' % os.path.basename(TARGET))
            return 1
        print('OK: %s matches src/' % os.path.basename(TARGET))
        return 0
    with open(TARGET, 'w', encoding='utf-8', newline='') as handle:
        handle.write(payload)
    print('WROTE: %s (%d bytes)' % (os.path.basename(TARGET), len(payload)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
