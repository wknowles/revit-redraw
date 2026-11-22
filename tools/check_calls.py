# -*- coding: utf-8 -*-
"""Check that every call to a locally defined function passes the right number
of arguments.

    python3 tools/check_calls.py

Dynamo embeds the graph's Python in a JSON string, so a wrong argument count
does not show up until the graph runs in Revit - and if the failing call is in
a branch the operator does not take, it can stay hidden for months. This walks
the AST of every source in the repository and compares each call against the
signature of the function it calls.
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def sources():
    found = []
    for name in sorted(os.listdir(ROOT)):
        if name.endswith('.dyn'):
            with open(os.path.join(ROOT, name), encoding='utf-8-sig') as handle:
                graph = json.load(handle)
            for node in graph.get('Nodes', []):
                if node.get('NodeType') == 'PythonScriptNode':
                    found.append((name, node.get('Code', '')))
    for folder, name in [('phase0', 'phase0_probe.py'), ('src', 'graph2.py'),
                         ('src', 'merge_core.py')]:
        path = os.path.join(ROOT, folder, name)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as handle:
                found.append((folder + '/' + name, handle.read()))
    return found


def signatures(tree):
    """name -> (required, maximum) argument counts."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            positional = [a.arg for a in node.args.args
                          if a.arg not in ('self', 'cls')]
            required = len(positional) - len(node.args.defaults)
            optional = len(node.args.defaults)
            required += len(node.args.kwonlyargs)
            if node.args.kwarg:
                optional += 1
            out[node.name] = (max(required, 0), required + optional)
    return out


def check(label, code):
    problems = []
    try:
        tree = ast.parse(code)
    except SyntaxError as ex:
        return ['%s: does not parse (%s)' % (label, ex)]
    defined = signatures(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name):
            continue
        name = func.id
        if name not in defined:
            continue
        required, maximum = defined[name]
        supplied = len(node.args)
        has_star = any(isinstance(a, ast.Starred) for a in node.args)
        has_kwargs = node.keywords and any(k.arg is None for k in node.keywords)
        if has_star or has_kwargs:
            continue
        if supplied < required:
            problems.append('%s:%d: %s() takes %d argument(s) but %d were given'
                            % (label, node.lineno, name, required, supplied))
        elif supplied > maximum:
            problems.append('%s:%d: %s() takes at most %d argument(s) but %d were given'
                            % (label, node.lineno, name, maximum, supplied))
    return problems


def main():
    problems = []
    for label, code in sources():
        problems.extend(check(label, code))
    for problem in problems:
        print('ERROR   ' + problem)
    print('\n%s call signature problem(s)' % len(problems))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
