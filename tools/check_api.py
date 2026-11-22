# -*- coding: utf-8 -*-
"""Audit the repository's Revit API usage against the Revit 2027 documentation.

    python3 tools/check_api.py

Three checks:

  1. Every Autodesk.Revit type the repository imports must exist in the API.
  2. Every ``Type.Member`` access, where Type is an imported Revit type, must
     name a member that type actually declares. This is exact.
  3. Every other ``.Member`` access whose name is PascalCase must correspond to
     some documented Revit member. This is a heuristic - it cannot tell which
     type a variable holds - so it is reported as a warning.

Check 2 is the one that catches mistakes like ``BuiltInCategory.OST_GenericAnnotations``
or ``XYZ.Length``: both name a member the type does not have in Revit 2027.

The API index is tools/revit_api_2027.json, refreshed with
``python3 tools/build_api_index.py``.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(HERE, 'revit_api_2027.json')

MEMBER = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)')
IMPORT = re.compile(r'from\s+(Autodesk\.Revit[A-Za-z.]*)\s+import\s*\(([^)]*)\)', re.S)
IMPORT_ONE = re.compile(r'from\s+(Autodesk\.Revit[A-Za-z.]*)\s+import\s+([A-Za-z_,\s]+)')
STRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\n]*"|\'[^\'\n]*\'')
COMMENT = re.compile(r'#[^\n]*')

# Owners that are not Revit API types, so their members are not audited.
FOREIGN = {
    'clr', 'RevitServices', 'Autodesk', 'Revit', 'Persistence', 'os', 'sys', 're',
    'json', 'math', 'tempfile', 'datetime', 'timezone', 'uuid', 'shutil', 'subprocess',
    'DocumentManager', 'CurrentDBDocument', 'Instance', 'currentVersion',
}

# .NET members that are not part of the Revit API.
DOTNET = {'GetType', 'ToString', 'Equals', 'GetHashCode', 'Dispose'}


def sources():
    """Every Python source in the repository, with a label."""
    found = []
    for name in sorted(os.listdir(ROOT)):
        if name.endswith('.dyn'):
            with open(os.path.join(ROOT, name), encoding='utf-8-sig') as handle:
                graph = json.load(handle)
            for node in graph.get('Nodes', []):
                if node.get('NodeType') == 'PythonScriptNode':
                    found.append((name, node.get('Code', '')))
    for folder, name in [('phase0', 'phase0_probe.py')]:
        path = os.path.join(ROOT, folder, name)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as handle:
                found.append((folder + '/' + name, handle.read()))
    return found


def strip_noise(source):
    source = STRING.sub(' ', source)
    return COMMENT.sub(' ', source)


def imported_types(source):
    found = set()
    for match in IMPORT.finditer(source):
        for token in match.group(2).replace('\n', ' ').split(','):
            token = token.strip()
            if token:
                found.add(token.split('.')[-1])
    for match in IMPORT_ONE.finditer(source):
        for token in match.group(2).split(','):
            token = token.strip()
            if token:
                found.add(token.split('.')[-1])
    return found


def main():
    if not os.path.isfile(INDEX):
        print('missing %s - run: python3 tools/build_api_index.py' % INDEX)
        return 2
    with open(INDEX, encoding='utf-8') as handle:
        index = json.load(handle)
    types = index['types']

    known_members = set()
    for entry in types.values():
        known_members.update(entry['own'])
        known_members.update(entry['inherited'])
        known_members.update(entry['enum'])

    errors = []
    warnings = []
    for label, source in sources():
        clean = strip_noise(source)
        imports = imported_types(clean)

        for name in sorted(imports):
            if name not in types:
                errors.append('%s: imports %s, which is not in the Revit 2027 API' % (label, name))

        for match in MEMBER.finditer(clean):
            owner, member = match.group(1), match.group(2)
            if owner in imports:
                entry = types.get(owner)
                if entry is None:
                    continue
                allowed = set(entry['own']) | set(entry['inherited']) | set(entry['enum'])
                if member not in allowed:
                    errors.append('%s: %s.%s is not a member of %s in Revit 2027'
                                  % (label, owner, member, owner))
            elif (member[:1].isupper() and member not in known_members
                  and member not in DOTNET
                  and owner not in FOREIGN and owner not in imports):
                warnings.append('%s: %s.%s does not correspond to any documented Revit member'
                                % (label, owner, member))

    for message in errors:
        print('ERROR   ' + message)
    for message in warnings:
        print('WARNING ' + message)
    print('\n%s errors, %s warnings (checked %d source files against %d documented types)'
          % (len(errors), len(warnings), len(sources()), len(types)))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
