"""Audit every historical source version reachable from upstream PRs and fork branches."""
import json
import os
from pathlib import Path
import re
import subprocess

def git(*args, input=None):
    return subprocess.check_output(['git', '-c', 'core.quotepath=false', *args], input=input, text=True)

root = Path(__file__).resolve().parents[1]
os.chdir(root)
snapshot = json.loads((root / 'validation/sse-input-snapshot.json').read_text())
rows = git('for-each-ref', '--format=%(refname)\t%(objectname)', 'refs/audit/fork/', 'refs/audit/upstream/', 'refs/audit/upstream-pr/').splitlines()
refs = {name: sha for name, sha in (row.split('\t') for row in rows)
        if name != 'refs/audit/fork/validation/sse-event-framing-20261116'}
fork = {name.removeprefix('refs/audit/fork/'): sha for name, sha in refs.items() if name.startswith('refs/audit/fork/')}
pulls = {int(name.rsplit('/', 1)[1]): sha for name, sha in refs.items() if name.startswith('refs/audit/upstream-pr/')}
assert len(pulls) >= len(snapshot['pulls']), (len(pulls), len(snapshot['pulls']))
assert {p['number'] for p in snapshot['pulls']} <= pulls.keys()
assert {b['name'] for b in snapshot['fork_branches']} <= fork.keys()
for branch in snapshot['fork_branches']:
    git('cat-file', '-e', branch['sha'] + '^{commit}')
for pr in snapshot['pulls']:
    git('cat-file', '-e', pr['head'] + '^{commit}')
revisions = sorted(set(refs.values()))
history = git('log', '--full-history', '--format=@@COMMIT %H', '--name-status', '--find-renames=40%', *revisions)
changes = []
commit = None
for line in history.splitlines():
    if line.startswith('@@COMMIT '):
        commit = line.split()[1]
    elif line and commit:
        fields = line.split('\t')
        if len(fields) >= 2:
            changes.append({'commit': commit, 'status': fields[0], 'paths': fields[1:]})
aliases = {
    'edge0-infer/tools/cli/serve_delegate_status.cpp',
    'edge0-infer/tools/cli/serve_delegate_status.h',
    'edge0-infer/tools/cli/serve_delegate_proxy.cpp',
}
for change in changes:
    for path in change['paths']:
        if Path(path).name in {'serve_delegate_status.cpp', 'serve_delegate_status.h', 'serve_delegate_proxy.cpp'}:
            aliases.add(path)
while True:
    before = set(aliases)
    for change in changes:
        if change['status'].startswith(('R', 'C')) and aliases.intersection(change['paths']):
            aliases.update(change['paths'])
    if aliases == before:
        break
selected = [change for change in changes if aliases.intersection(change['paths'])]
objects = {}
for line in git('rev-list', '--objects', *revisions, '--', *sorted(aliases)).splitlines():
    parts = line.split(' ', 1)
    if len(parts) == 2 and parts[1] in aliases:
        objects.setdefault(parts[0], set()).add(parts[1])
types = git('cat-file', '--batch-check=%(objectname) %(objecttype)', input=''.join(sha + '\n' for sha in objects))
versions = []
for line in types.splitlines():
    sha, kind = line.split()
    if kind != 'blob':
        continue
    source = git('cat-file', '-p', sha)
    match = re.search(r'void account_sse\([^)]*\)\s*\{.*?^\}', source, re.S | re.M)
    version = {'blob': sha, 'paths': sorted(objects[sha]), 'source': source}
    if match:
        version['account_sse'] = match.group(0)
        version['per_line_parser_markers'] = "pending.find('\\n', start)" in match.group(0) and 'pending.erase(0, start)' in match.group(0)
    versions.append(version)
base_blob = git('rev-parse', snapshot['base'] + ':edge0-infer/tools/cli/serve_delegate_status.cpp').strip()
desktop_blob = git('rev-parse', '9706e5b570008e3e7e347a6e026a20112924ff86:edge0-infer/tools/cli/serve_delegate_status.cpp').strip()
assert base_blob == desktop_blob, '#249 unexpectedly changes the native parser'
report = {
    'base': snapshot['base'], 'all_author_prs': len(snapshot['pulls']),
    'upstream_pr_refs': len(pulls), 'fork_branch_refs': len(fork),
    'pre_task_fork_branches': [name for name in fork if name == 'main' or '/20261111' in name],
    'refs': refs, 'reachable_commits': int(git('rev-list', '--count', *revisions)),
    'aliases': sorted(aliases), 'relevant_changes': selected, 'source_versions': versions,
    'pr249_native_parser_unchanged': base_blob, 'production_candidate_excluded_from_history': True,
}
(root / 'audit').mkdir(exist_ok=True)
(root / 'audit/edge0-sse-history.json').write_text(json.dumps(report, indent=2) + '\n')
summary = {key: value for key, value in report.items() if key not in {'refs', 'relevant_changes', 'source_versions'}}
summary['source_version_count'] = len(versions)
summary['parser_versions'] = [{'blob': v['blob'], 'paths': v['paths'], 'per_line_parser_markers': v.get('per_line_parser_markers')}
                              for v in versions if 'account_sse' in v]
print('SSE_HISTORY_SUMMARY=' + json.dumps(summary))
print('SSE_HISTORY_REFS=' + json.dumps(refs))
print('SSE_HISTORY_CHANGES=' + json.dumps(selected))
for version in versions:
    print('SSE_HISTORY_VERSION=' + json.dumps(version))
