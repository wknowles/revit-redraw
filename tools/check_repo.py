# -*- coding: utf-8 -*-
"""Run every static check in the repository.

    python3 tools/check_repo.py

  1. the .dyn files parse as JSON and keep CRLF line endings
  2. the Python embedded in each .dyn compiles
  3. phase0/Phase 0 Prototype.dyn matches phase0/phase0_probe.py
  4. the phase 0 probe survives its stubbed Revit models
  5. every Revit API call matches the Revit 2027 documentation

None of these need Revit. Check 5 uses tools/revit_api_2027.json, which is
refreshed with python3 tools/build_api_index.py.
"""
import json
import os
import py_compile
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYTHON = sys.executable


def graphs():
    for name in sorted(os.listdir(ROOT)):
        if name.endswith('.dyn'):
            yield name, os.path.join(ROOT, name)


def check_graphs():
    failures = []
    for name, path in graphs():
        with open(path, encoding='utf-8-sig', newline='') as handle:
            raw = handle.read()
        if '\r\n' not in raw:
            failures.append('%s: no CRLF line endings' % name)
        elif raw.count('\n') != raw.count('\r\n'):
            failures.append('%s: mixed line endings' % name)
        try:
            graph = json.loads(raw)
        except Exception as ex:
            failures.append('%s: invalid JSON (%s)' % (name, ex))
            continue
        for node in graph.get('Nodes', []):
            if node.get('NodeType') != 'PythonScriptNode':
                continue
            code = node.get('Code', '')
            if code.count('\r\n') != code.count('\n'):
                failures.append('%s: embedded Python is not CRLF' % name)
            handle = tempfile.NamedTemporaryFile('w', suffix='.py', delete=False,
                                                  encoding='utf-8', newline='')
            handle.write(code)
            handle.close()
            try:
                py_compile.compile(handle.name, doraise=True)
            except Exception as ex:
                failures.append('%s: embedded Python does not compile (%s)' % (name, ex))
            finally:
                os.unlink(handle.name)
    return failures


def run(label, argv):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    ok = result.returncode == 0
    print('%-46s %s' % (label, 'PASS' if ok else 'FAIL'))
    if not ok:
        output = (result.stdout + result.stderr).strip()
        for line in output.splitlines():
            print('    ' + line)
    return [] if ok else [label]


def main():
    failures = []

    problems = check_graphs()
    print('%-46s %s' % ('graphs are valid CRLF JSON with compiling Python',
                        'PASS' if not problems else 'FAIL'))
    for problem in problems:
        print('    ' + problem)
    failures.extend(problems)

    failures.extend(run('phase0 graph matches phase0_probe.py',
                        [PYTHON, os.path.join(ROOT, 'phase0', 'build_dyn.py'), '--check']))
    failures.extend(run('graph 2 matches src/',
                        [PYTHON, os.path.join(ROOT, 'src', 'build_graph2.py'), '--check']))
    failures.extend(run('phase0 probe survives its stubbed models',
                        [PYTHON, os.path.join(ROOT, 'phase0', 'test_probe.py')]))
    failures.extend(run('merge core unit tests',
                        [PYTHON, os.path.join(ROOT, 'src', 'test_merge_core.py')]))
    failures.extend(run('graph 2 planning tests',
                        [PYTHON, os.path.join(ROOT, 'src', 'test_graph2_plan.py')]))
    failures.extend(run('every call passes the right arguments',
                        [PYTHON, os.path.join(HERE, 'check_calls.py')]))
    failures.extend(run('Revit API usage matches the 2027 documentation',
                        [PYTHON, os.path.join(HERE, 'check_api.py')]))

    print('\n%s' % ('ALL CHECKS PASSED' if not failures else '%d CHECK(S) FAILED' % len(failures)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
