"""Exercise the shipped worker CLI and Aider 0.86.2's actual config parser backend."""
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


class ContextHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith('/api/context/inject?'):
            self.send_error(404)
            return
        body = b'# Native config fixture\n\nUse SQLite for local persistence.\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/markdown; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def parse_read(root_config: Path, app_config: Path) -> list[str]:
    # Aider v0.86.2 main.py reverses cwd/root/home before the final get_parser;
    # args.py uses YAMLConfigFileParser and registers --read with action=append.
    parser = configargparse.ArgumentParser(
        default_config_files=[str(root_config), str(app_config)],
        config_file_parser_class=configargparse.YAMLConfigFileParser,
        auto_env_var_prefix='AIDER_',
    )
    parser.add_argument('--read', action='append', metavar='FILE')
    return parser.parse_args([]).read


def exercise(bundle: Path, fixed: bool, port: int) -> dict:
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp-claude-mem') as directory:
        root = Path(directory) / 'project'
        app = root / 'packages/app'
        app.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        root_config, app_config = root / '.aider.conf.yml', app / '.aider.conf.yml'
        original_root, original_app = 'read: [ROOT.md]\n', 'read: [APP.md]\n'
        root_config.write_text(original_root)
        app_config.write_text(original_app)
        (root / 'ROOT.md').write_text('Root conventions\n')
        (app / 'APP.md').write_text('App conventions\n')
        env = dict(os.environ, CLAUDE_MEM_DATA_DIR=str(Path(directory) / 'data'),
                   CLAUDE_MEM_WORKER_PORT=str(port), CLAUDE_MEM_WORKER_HOST='127.0.0.1',
                   TMPDIR=str(ROOT / 'tmp-claude-mem'), NO_COLOR='1')

        def cli(verb):
            return subprocess.run([str(BUN), str(bundle), 'aider', verb, '--no-auto-start',
                                   '--cwd', str(app)], env=env, cwd=REPO,
                                  text=True, capture_output=True, timeout=30)

        installed = cli('install')
        assert installed.returncode == 0, installed.stderr
        selected_read = parse_read(root_config, app_config)
        memory = app / '.claude-mem/aider-memory.md'
        result = {
            'bundle': bundle.name,
            'install_exit': installed.returncode,
            'effective_read': selected_read,
            'root_config_unchanged': root_config.read_text() == original_root,
            'memory_exists_at_aider_resolved_path': memory.is_file(),
        }
        assert ('.claude-mem/aider-memory.md' in selected_read) is fixed
        assert result['root_config_unchanged'] is fixed
        assert memory.is_file() is fixed
        if fixed:
            assert 'Use SQLite for local persistence.' in memory.read_text()
            result['status_exit'] = cli('status').returncode
            result['uninstall_exit'] = cli('uninstall').returncode
            assert result['status_exit'] == result['uninstall_exit'] == 0
            assert root_config.read_text() == original_root
            assert app_config.read_text() == original_app
            result['uninstall_restored_both_configs'] = True
        return result


baseline = REPO / 'plugin/scripts/worker-service-before-precedence.cjs'
baseline.write_bytes(subprocess.check_output(['git', 'show', 'HEAD:plugin/scripts/worker-service.cjs'], cwd=REPO))
server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ContextHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    results = {
        'host_reference': 'Aider-AI/aider v0.86.2 aider/main.py and aider/args.py',
        'configargparse': '1.7.1',
        'pyyaml': yaml.__version__,
        'before': exercise(baseline, False, server.server_port),
        'after': exercise(REPO / 'plugin/scripts/worker-service.cjs', True, server.server_port),
        'external_model_calls': 0,
    }
    print(json.dumps(results, indent=2))
finally:
    server.shutdown()
    server.server_close()
    baseline.unlink()
