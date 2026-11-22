# -*- coding: utf-8 -*-
"""Build "Phase 0 Prototype.dyn" from phase0_probe.py.

The .dyn file is the deliverable the operator opens in Dynamo; phase0_probe.py
is the source of truth so the Python can be reviewed and diffed as plain text.

    python3 phase0/build_dyn.py          # regenerate the .dyn
    python3 phase0/build_dyn.py --check  # fail if the .dyn is out of date

Dynamo graph files are CRLF JSON with the embedded Python stored as escaped
\r\n strings, matching the two workflow graphs in the repository root.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, 'phase0_probe.py')
TARGET = os.path.join(HERE, 'Phase 0 Prototype.dyn')

GRAPH_UUID = 'b7d4e2a1-5c38-4f6b-9e07-2ad3c1f8b640'

PYTHON_NODE = '3e8b1c47a5d24f0e9b6c7d18a2f4e5309'
PYTHON_OUT = '5a1f9c3d7e2b48d0b6f1a4c8e3d50792'
PYTHON_IN = '6b2e4d8a1c7f4390b5e2d6a8f1c37045'

BOOL_NODE = '4c9f1e5b8d36a2705e4b9c1f3d86a479'
BOOL_OUT = '5d0a2f6c9e47b3816f5c0d2a4e97b58a'

WATCH_NODE = '7ea2d5c9e4b13f60a8d3c7b1e5f20486'
WATCH_IN = '8fb3e6d0a5c24071b9e4d8c2f6031597'
WATCH_OUT = '90c4f7e1b6d35182c0f5e9d3a71426a8'

CONNECTOR_PY_BOOL = 'a1d5c8f2b6e94a730c8d1f5b9e3a70426'
CONNECTOR_PY_WATCH = 'b2e6d9a3c7f04b81d9e2a6c0f4b81537'


def port(name, description):
    return {'Id': name, 'Name': '', 'Description': description,
            'UsingDefaultValue': False, 'Level': 2,
            'UseLevels': False, 'KeepListStructure': False}


def build_graph():
    with open(SOURCE, 'r', encoding='utf-8') as handle:
        code = handle.read()
    code = code.replace('\r\n', '\n').replace('\n', '\r\n')

    python_node = {
        'ConcreteType': 'PythonNodeModels.PythonNode, PythonNodeModels',
        'Engine': 'PythonNet3',
        'VariableInputPorts': True,
        'Id': PYTHON_NODE,
        'NodeType': 'PythonScriptNode',
        'Inputs': [
            {'Id': PYTHON_IN, 'Name': 'IN[0]', 'Description': 'Input #0',
             'UsingDefaultValue': False, 'Level': 2,
             'UseLevels': False, 'KeepListStructure': False},
        ],
        'Outputs': [
            {'Id': PYTHON_OUT, 'Name': 'OUT', 'Description': 'Result of the python script',
             'UsingDefaultValue': False, 'Level': 2,
             'UseLevels': False, 'KeepListStructure': False},
        ],
        'Replication': 'Disabled',
        'Description': 'Runs an embedded Python script.',
        'Code': code,
    }

    bool_node = {
        'ConcreteType': 'CoreNodeModels.Input.BoolSelector, CoreNodeModels',
        'Id': BOOL_NODE,
        'NodeType': 'BooleanInputNode',
        'Inputs': [],
        'Outputs': [port(BOOL_OUT, 'Boolean')],
        'Replication': 'Disabled',
        'Description': 'Enables selection between True and False',
        'InputValue': False,
    }

    watch_node = {
        'ConcreteType': 'CoreNodeModels.Watch, CoreNodeModels',
        'WatchWidth': 500.0,
        'WatchHeight': 420.0,
        'Id': WATCH_NODE,
        'NodeType': 'ExtensionNode',
        'Inputs': [port(WATCH_IN, 'Node to show output from')],
        'Outputs': [port(WATCH_OUT, 'Node output')],
        'Replication': 'Disabled',
        'Description': 'Visualizes a node\'s output',
    }

    graph = {
        'Uuid': GRAPH_UUID,
        'IsCustomNode': False,
        'Description': 'Phase 0 probe: answers the runtime questions the Revit API '
                       'documentation cannot answer before the Redraw merge is rebuilt.',
        'Name': 'Phase 0 Prototype',
        'ElementResolver': {'ResolutionMap': {}},
        'Inputs': [
            {'Id': BOOL_NODE, 'Name': 'Run Visual Test', 'Type': 'boolean',
             'Type2': 'boolean', 'Value': 'false',
             'Description': 'False runs diagnostics only and changes nothing. True commits '
                            'one transaction that draws a blue band and a green wall so the '
                            'result can be looked at. Press Ctrl+Z once afterwards.'},
        ],
        'Outputs': [
            {'Id': WATCH_NODE, 'Name': 'Probe Log', 'Type': 'unknown', 'InitialValue': '',
             'Description': 'Visualizes a node\'s output'},
        ],
        'Nodes': [python_node, bool_node, watch_node],
        'Connectors': [
            {'Start': BOOL_OUT, 'End': PYTHON_IN, 'Id': CONNECTOR_PY_BOOL, 'IsHidden': 'False'},
            {'Start': PYTHON_OUT, 'End': WATCH_IN, 'Id': CONNECTOR_PY_WATCH, 'IsHidden': 'False'},
        ],
        'Dependencies': [],
        'NodeLibraryDependencies': [],
        'EnableLegacyPolyCurveBehavior': True,
        'Thumbnail': '',
        'GraphDocumentationURL': None,
        'ExtensionWorkspaceData': [
            {'ExtensionGuid': '28992e1d-abb9-417f-8b1b-05e053bee670',
             'Name': 'Properties', 'Version': '4.0', 'Data': {}},
            {'ExtensionGuid': 'DFBD9CC0-DB40-457A-939E-8C8555555A9D',
             'Name': 'Generative Design', 'Version': '10.3', 'Data': {}},
        ],
        'Author': '',
        'Linting': {'activeLinter': 'None',
                    'activeLinterId': '7b75fb44-43fd-4631-a878-29f4d5d8399a',
                    'warningCount': 0, 'errorCount': 0},
        'Bindings': [],
        'View': {
            'Dynamo': {'ScaleFactor': 1.0, 'HasRunWithoutCrash': True,
                       'IsVisibleInDynamoLibrary': True, 'Version': '4.0.2.3852',
                       'RunType': 'Manual', 'RunPeriod': '1000'},
            'Camera': {'Name': '_Background Preview', 'EyeX': -17.0, 'EyeY': 24.0,
                       'EyeZ': 50.0, 'LookX': 12.0, 'LookY': -13.0, 'LookZ': -58.0,
                       'UpX': 0.0, 'UpY': 1.0, 'UpZ': 0.0},
            'ConnectorPins': [],
            'NodeViews': [
                {'Id': BOOL_NODE, 'Name': 'Run Visual Test', 'IsSetAsInput': True,
                 'IsSetAsOutput': False, 'Excluded': False, 'ShowGeometry': True,
                 'X': 120.0, 'Y': 120.0},
                {'Id': PYTHON_NODE, 'Name': 'Phase 0 Probe', 'IsSetAsInput': False,
                 'IsSetAsOutput': False, 'Excluded': False, 'ShowGeometry': True,
                 'X': 430.0, 'Y': 190.0},
                {'Id': WATCH_NODE, 'Name': 'Probe Log', 'IsSetAsInput': False,
                 'IsSetAsOutput': True, 'Excluded': False, 'ShowGeometry': True,
                 'X': 900.0, 'Y': 190.0},
            ],
            'Annotations': [],
            'X': -523.4938468005303,
            'Y': -8.356583784257623,
            'Zoom': 0.879394765673039,
        },
    }
    return graph


def serialise(graph):
    text = json.dumps(graph, indent=2, ensure_ascii=False)
    return text.replace('\n', '\r\n')


def main():
    graph = build_graph()
    payload = serialise(graph)
    if '--check' in sys.argv:
        if not os.path.isfile(TARGET):
            print('MISSING: %s' % TARGET)
            return 1
        with open(TARGET, 'r', encoding='utf-8', newline='') as handle:
            current = handle.read()
        if current != payload:
            print('OUT OF DATE: %s does not match phase0_probe.py' % TARGET)
            return 1
        print('OK: %s is up to date' % TARGET)
        return 0
    with open(TARGET, 'w', encoding='utf-8', newline='') as handle:
        handle.write(payload)
    print('WROTE: %s (%d bytes)' % (TARGET, len(payload)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
