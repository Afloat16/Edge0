"""Run installed CLI edits through Aider's ConfigArgParse/PyYAML backend."""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path('/workspace/scratch/4bed741d4966')
REPO = ROOT / 'claude-mem-contributions-20261116'
BUN = ROOT / 'claude-mem-runtime/node_modules/@oven/bun-linux-x64/bin/bun'
sys.path.insert(0, str(ROOT / 'claude-mem-aider-validation-deps'))
import configargparse
import yaml

MEMORY = '.claude-mem/aider-memory.md'
CASES = [
    ('plain apostrophe', "docs/user's-guide.md"),
    ('plain embedded quote', 'docs/user"guide.md'),
    ('quoted brackets', "'docs/[team], notes.md'"),
    ('escaped double quotes', r'"docs/team \"blue, green\".md"'),
    ('doubled single quote', "'docs/it''s, [shared].md'"),
    ('quoted comma control', '"docs/team, notes.md"'),
]


class ContextHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith('/api/context/inject?'):
            self.send_error(404)
            return
        body = b'# Native flow fixture\n\nUse SQLite for local persistence.\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/markdown; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def parse_read(config: Path) -> list[str]:
    parser = configargparse.ArgumentParser(
        default_config_files=[str(config)],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
    )
    parser.add_argument('--read', action='append', metavar='FILE')
    return parser.parse_args([]).read


def exercise(bundle: Path, fixed: bool, port: int) -> list[dict]:
    results = []
    for name, scalar in CASES:
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp-claude-mem') as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            config = project / '.aider.conf.yml'
            original = f'read: [{scalar}, NOTES.md] # [shared docs]\n'
            config.write_text(original)
            expected = parse_read(config)
            for filename in expected:
                file = project / filename
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(f'Conventions from {filename}\n')
            env = dict(os.environ, CLAUDE_MEM_DATA_DIR=str(root / 'data'),
                       CLAUDE_CONFIG_DIR=str(root / 'claude'), CLAUDE_MEM_TELEMETRY='0',
                       CLAUDE_MEM_WORKER_PORT=str(port), CLAUDE_MEM_WORKER_HOST='127.0.0.1',
                       TMPDIR=str(ROOT / 'tmp-claude-mem'), NO_COLOR='1')

            def cli(verb):
                return subprocess.run([str(BUN), str(bundle), 'aider', verb, '--no-auto-start',
                                       '--cwd', str(project)], env=env, cwd=REPO,
                                      text=True, capture_output=True, timeout=30)

            installed = cli('install')
            result = {'case': name, 'native_initial_read': expected,
                      'install_exit': installed.returncode,
                      'config_unchanged_after_install': config.read_text() == original}
            if installed.returncode != 0:
                result['diagnostic'] = installed.stderr.strip()
                assert not fixed
                assert config.read_text() == original
                assert parse_read(config) == expected
                results.append(result)
                continue

            result['native_installed_read'] = parse_read(config)
            assert result['native_installed_read'] == expected + [MEMORY]
            assert 'Use SQLite for local persistence.' in (project / MEMORY).read_text()
            result['status_exit'] = cli('status').returncode
            installed_text = config.read_text()
            assert cli('install').returncode == 0
            result['second_install_unchanged'] = config.read_text() == installed_text
            assert result['second_install_unchanged']
            uninstalled = cli('uninstall')
            result['uninstall_exit'] = uninstalled.returncode
            result['native_uninstalled_read'] = parse_read(config)
            result['original_paths_preserved'] = result['native_uninstalled_read'] == expected
            result['all_remaining_files_exist'] = all((project / p).is_file() for p in result['native_uninstalled_read'])
            if fixed:
                assert installed.returncode == result['status_exit'] == uninstalled.returncode == 0
                assert result['original_paths_preserved'] and result['all_remaining_files_exist']
                assert '# [shared docs]' in config.read_text()
            results.append(result)
    return results


baseline = REPO / 'plugin/scripts/worker-service-before-flow.cjs'
baseline.write_bytes(subprocess.check_output(['git', 'show', '82d3602e2e43503ffd39c180b2af5f7521fc457b:plugin/scripts/worker-service.cjs'], cwd=REPO))
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ContextHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    before = exercise(baseline, False, server.server_port)
    after = exercise(REPO / 'plugin/scripts/worker-service.cjs', True, server.server_port)
    assert sum(item['install_exit'] != 0 for item in before) == 4
    assert sum(item.get('original_paths_preserved') is False for item in before) == 1
    print(json.dumps({'host_reference': 'Aider-AI/aider v0.86.2 args.py YAMLConfigFileParser and --read action=append',
                      'configargparse': '1.7.1', 'pyyaml': yaml.__version__,
                      'before': before, 'after': after, 'external_model_calls': 0}, indent=2))
finally:
    server.shutdown()
    server.server_close()
    baseline.unlink()
