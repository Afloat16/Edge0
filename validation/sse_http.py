"""Run the real native proxy against the repository's HTTP fixture."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path('tests/integration').resolve()))
import serve_strata_test as tests

parser = argparse.ArgumentParser()
parser.add_argument('--expect-failure', action='store_true')
parser.add_argument('--label', required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory() as folder:
    try:
        tests.event_framing(str(Path('build/cpu/bin/edge0-infer').resolve()), Path(folder))
    except AssertionError as error:
        if not args.expect_failure:
            raise
        failures = error.args[0]
        assert isinstance(failures, list), repr(error)
        ordinary = {(item['endpoint'], item['case']) for item in failures}
        paths = {'/v1/chat/completions', '/v1/responses', '/v1/messages'}
        assert {(path, 'multiline_lf') for path in paths} <= ordinary, failures
        assert {(path, 'no_final_blank_line') for path in paths} <= ordinary, failures
        print('SSE_HTTP_BASELINE=' + json.dumps({'label': args.label, 'failures': failures}))
    else:
        assert not args.expect_failure, 'The expected native HTTP failure did not reproduce'
        print('SSE_HTTP_FIXED=' + json.dumps({'label': args.label, 'cases': 15, 'protocols': 3}))
