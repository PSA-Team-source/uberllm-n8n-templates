#!/usr/bin/env python3
"""Structure check for every workflow in ../workflows (run after build.py).

Fails on what would break an import or a run in n8n: missing node fields,
duplicate node names, a connection to a node that does not exist, an HTTP node
to the UberLLM API without the shared credential, or an API key in a file.
The real test is importing and running them in n8n (see README).
"""
import json
import re
import sys
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / 'workflows'
NEED = ('id', 'name', 'type', 'typeVersion', 'position', 'parameters')
problems = []

for f in sorted(WF.glob('*.json')):
    raw = f.read_text()
    if re.search(r'\bull[-_][A-Za-z0-9]{16,}', raw):
        problems.append(f'{f.name}: looks like it contains an API key')
    wf = json.loads(raw)
    for k in ('name', 'nodes', 'connections'):
        if k not in wf:
            problems.append(f'{f.name}: no "{k}"')
    names = [n.get('name') for n in wf['nodes']]
    if len(names) != len(set(names)):
        problems.append(f'{f.name}: duplicate node names')
    ids = [n.get('id') for n in wf['nodes']]
    if len(ids) != len(set(ids)):
        problems.append(f'{f.name}: duplicate node ids')
    for n in wf['nodes']:
        missing = [k for k in NEED if k not in n]
        if missing:
            problems.append(f'{f.name}: node {n.get("name")} lacks {missing}')
        if n['type'] == 'n8n-nodes-base.httpRequest' and 'api.uberllm.dev' in json.dumps(n['parameters']) + n['parameters'].get('url', ''):
            if n['parameters'].get('genericAuthType') != 'httpHeaderAuth' or 'httpHeaderAuth' not in n.get('credentials', {}):
                problems.append(f'{f.name}: {n["name"]} calls UberLLM without the Header Auth credential')
    if not any(n['type'] == 'n8n-nodes-base.manualTrigger' for n in wf['nodes']):
        problems.append(f'{f.name}: no trigger')
    for src, outs in wf['connections'].items():
        if src not in names:
            problems.append(f'{f.name}: connection from unknown node {src}')
        for branch in outs.get('main', []):
            for c in branch:
                if c['node'] not in names:
                    problems.append(f'{f.name}: {src} connects to unknown node {c["node"]}')
    # Every non-note node is reachable from the trigger.
    seen, todo = set(), [n['name'] for n in wf['nodes'] if n['type'].endswith('manualTrigger')]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for branch in wf['connections'].get(cur, {}).get('main', []):
            todo += [c['node'] for c in branch]
    orphans = [n['name'] for n in wf['nodes'] if n['name'] not in seen and not n['type'].endswith('stickyNote')]
    if orphans:
        problems.append(f'{f.name}: not connected: {orphans}')

if problems:
    print('\n'.join(problems))
    sys.exit(1)
print(f'ok: {len(list(WF.glob("*.json")))} workflows')
