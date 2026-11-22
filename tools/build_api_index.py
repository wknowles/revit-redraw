# -*- coding: utf-8 -*-
"""Build the Revit API index used by check_api.py.

Reads the Revit 2027 API documentation and caches the members of every
RevitAPI type the repository touches, so the audit runs offline afterwards.

    python3 tools/build_api_index.py          # refresh tools/revit_api_2027.json
    python3 tools/build_api_index.py --types XYZ,Wall

The index is a plain JSON file so a reviewer can diff it when the docs change.
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(HERE, 'revit_api_2027.json')
DOCS = 'https://rvtdocs.com/2027/'
TREE = 'https://rvtdocs.com/static/json_trees/sidebar_2027.json'

# Types the repository touches indirectly: the values that variables hold.
EXTRA_TYPES = [
    'Application', 'Document', 'Element', 'Room', 'SpatialElement', 'Curve', 'Solid',
    'View', 'Level', 'LocationCurve', 'LocationPoint', 'WallType', 'Material',
    'Parameter', 'BoundarySegment', 'Phase', 'ElementType', 'FillPattern',
    'ElementTransformUtils', 'ProjectLocation', 'GeometryObject', 'SolidOptions',
    'Line', 'Arc', 'CurveLoop', 'SpatialElementBoundaryOptions', 'Color', 'DirectShape',
    'GeometryCreationUtilities', 'OverrideGraphicSettings', 'TaskDialog',
    'RoomTag', 'Reference', 'Transform', 'UIApplication', 'UIDocument',
    "ProjectInformation", "Family", "ModelPathUtils", "WorksharingUtils",
    "Units", "ExternalFileReference",
]

OWN = re.compile(r'class="member-name-link is-own"[^>]*>([^<(]+)')
INHERITED = re.compile(r'class="member-name-link is-inherited"[^>]*>([^<(]+)')
ENUM = re.compile(r'type-icon enm"[^>]*>E</span>\s*([A-Za-z0-9_]+)')

# Some types live outside Autodesk.Revit.DB, and the sidebar tree does not
# always carry a page for them.
FALLBACK_NAMESPACES = [
    'Autodesk.Revit.DB', 'Autodesk.Revit.DB.Architecture', 'Autodesk.Revit.UI',
    'Autodesk.Revit.ApplicationServices', 'Autodesk.Revit.DB.ExternalService',
]


def fetch(url, tries=4):
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode('utf-8', 'replace')
        except Exception as ex:
            last = ex
            time.sleep(1.5 * (attempt + 1))
    raise last


def load_tree():
    """Map simple type name -> documentation FQN.

    The FQN is not always Autodesk.Revit.DB.<Type>: Room, for instance, lives in
    Autodesk.Revit.DB.Architecture in the 2027 documentation.
    """
    raw = fetch(TREE)
    tree = json.loads(raw)
    names = {}

    def walk(node):
        if node is None:
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        title = node.get('title')
        fqn = node.get('fqn')
        if isinstance(title, str) and isinstance(fqn, str) and fqn.startswith('Autodesk.Revit.DB'):
            simple = re.sub(r' (Class|Enumeration)$', '', title)
            simple = simple.split('.')[-1]
            names.setdefault(simple, fqn)
        for key in node:
            walk(node[key])

    walk(tree)
    return names


def index_type(fqn):
    html = fetch(DOCS + fqn)
    title = re.search(r'<title>([^<]*)</title>', html)
    kind = 'enum' if 'Enumeration' in (title.group(1) if title else '') else 'class'
    return {
        'fqn': fqn,
        'kind': kind,
        'own': sorted(set(m.strip() for m in OWN.findall(html))),
        'inherited': sorted(set(m.strip() for m in INHERITED.findall(html))),
        'enum': sorted(set(ENUM.findall(html))),
    }


def types_used_in_repo():
    """Every Autodesk.Revit type imported by the repository's Python."""
    found = set()
    sources = []
    for name in sorted(os.listdir(ROOT)):
        if name.endswith('.dyn'):
            with open(os.path.join(ROOT, name), encoding='utf-8-sig') as handle:
                graph = json.load(handle)
            for node in graph.get('Nodes', []):
                if node.get('NodeType') == 'PythonScriptNode':
                    sources.append((name, node.get('Code', '')))
    probe = os.path.join(ROOT, 'phase0', 'phase0_probe.py')
    if os.path.isfile(probe):
        with open(probe, encoding='utf-8') as handle:
            sources.append(('phase0/phase0_probe.py', handle.read()))
    for label, source in sources:
        for match in re.finditer(r'from\s+(Autodesk\.Revit[A-Za-z.]*)\s+import\s*\(([^)]*)\)',
                                 source, re.S):
            namespace = match.group(1)
            for token in match.group(2).replace('\n', ' ').split(','):
                token = token.strip()
                if token:
                    found.add(namespace + '.' + token)
        for match in re.finditer(r'from\s+(Autodesk\.Revit[A-Za-z.]*)\s+import\s+([A-Za-z_,\s]+)',
                                 source):
            for token in match.group(2).split(','):
                token = token.strip()
                if token:
                    found.add(match.group(1) + '.' + token)
    return sorted(found)


def main():
    names = load_tree()
    wanted = types_used_in_repo()
    if '--types' in sys.argv:
        wanted = ['Autodesk.Revit.DB.' + t for t in sys.argv[sys.argv.index('--types') + 1].split(',')]
    else:
        wanted = wanted + EXTRA_TYPES

    simple = []
    for fqn in wanted:
        simple.append(fqn.split('.')[-1])
    simple = sorted(set(simple))

    index = {'version': '2027', 'source': DOCS, 'types': {}}
    failures = []
    for name in simple:
        candidates = []
        if name in names:
            candidates.append(names[name])
        for namespace in FALLBACK_NAMESPACES:
            candidates.append(namespace + '.' + name)
        indexed = False
        for fqn in candidates:
            try:
                index['types'][name] = index_type(fqn)
                indexed = True
                break
            except Exception:
                continue
        if not indexed:
            failures.append('%s (no documentation page found)' % name)
        time.sleep(0.35)
    with open(INDEX, 'w', encoding='utf-8') as handle:
        json.dump(index, handle, indent=1, sort_keys=True)
        handle.write('\n')
    print('indexed %d types -> %s' % (len(index['types']), INDEX))
    if failures:
        print('could not index:')
        for failure in failures:
            print('  ' + failure)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
