"""Reproduce the newly observed concurrency assertion with a pristine upstream source tree."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess

root = Path('build/cpu/bin')
needle = 'observed_reads>0 && maximum_writing==1 && overlap && !violation'
def run_one(item):
    index, name = item
    result = subprocess.run([str(root / name)], capture_output=True, text=True)
    return {'attempt': index, 'code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}

def probe(name, attempts, workers, expected):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_one, [(i, name) for i in range(attempts)]))
    failures = [result for result in results if result['code']]
    for failure in failures:
        assert expected in failure['stderr'], failure
        print('SSE_UNMODIFIED_FAILURE=' + json.dumps({'test': name, 'workers': workers, **failure}))
    summary = {'attempts': attempts, 'workers': workers, 'failed': len(failures)}
    print('SSE_UNMODIFIED_PROBE=' + json.dumps({'test': name, **summary}))
    return summary

slots = [probe('moe_test_edge0_35b_slots_config', 100, 1, needle)]
if not slots[0]['failed']:
    slots.append(probe('moe_test_edge0_35b_slots_config', 300, 4, needle))
assert any(item['failed'] for item in slots), 'The new concurrency failure did not reproduce on unmodified upstream'
weights = probe('moe_test_weights_mixed', 100, 1, 'budget.current(exec::Purpose::resident_weights)')
summary = {
    'base': 'f6bfa5546bcf89919f86946ec669bcc7f4cd526d',
    'native_tree_exactly_upstream': True,
    'slots': slots, 'weights': weights,
    'slots_binary_sha256': hashlib.sha256((root / 'moe_test_edge0_35b_slots_config').read_bytes()).hexdigest(),
}
Path(os.environ['RUNNER_TEMP'], 'sse-unmodified-baseline.json').write_text(json.dumps(summary))
print('SSE_UNMODIFIED_BASELINE=' + json.dumps(summary))
