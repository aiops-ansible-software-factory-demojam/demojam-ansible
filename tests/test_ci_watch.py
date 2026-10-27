"""Exercise bounded Controller waits without a growing AO polling graph."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WatchTests(unittest.TestCase):
    def test_long_pending_author_merge_and_source_drift(self):
        state = {'polls': 0, 'merged_reads': 0, 'drift': False}
        session = 'a' * 32

        class Handler(BaseHTTPRequestHandler):
            def respond(self, body, status=200):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_GET(self):
                if self.path == '/candidate':
                    state['polls'] += 1
                    self.respond({'ready': True, 'ci_passed': state['polls'] >= 120,
                                  'ci_failed': False, 'validation_required': False,
                                  'head_sha': 'c' * 40, 'number': 2})
                elif self.path.startswith('/v1/sessions/'):
                    self.respond({'id': session, 'status': 'running', 'agent_name': 'automation-developer',
                                  'title': 'Invoice remediation issue 1', 'items': []})
                elif self.path == '/merge':
                    state['merged_reads'] += 1
                    self.respond({'error': 'tested head changed'} if state['drift'] else
                                 {'merged': state['merged_reads'] >= 2}, 400 if state['drift'] else 200)
                else:
                    self.respond({}, 404)

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.respond({'access_token': 'fixture'})

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f'http://127.0.0.1:{server.server_port}'
        values = {'ci_repository': 'demo-owner/ansible-collection-demo.pythonapp', 'ci_issue_number': 1,
                  'ci_author_session': session, 'ci_gate': url + '/candidate', 'ci_agent_api': url,
                  'ci_watch_attempts': 65, 'ci_watch_delay': 0, 'ci_merge_gate': url + '/merge',
                  'ci_candidate_sha': 'c' * 40, 'ci_pr_number': 2, 'ci_merge_attempts': 3, 'ci_merge_delay': 0}
        try:
            def run(playbook):
                return subprocess.run(['ansible-playbook', '-i', 'localhost,', str(ROOT / 'playbooks/ci' / playbook),
                                       '-e', json.dumps(values)], text=True, capture_output=True,
                                      env=dict(os.environ, OMNIGENT_CI_CLIENT_ID='fixture',
                                               OMNIGENT_CI_CLIENT_SECRET='fixture',
                                               ANSIBLE_SHOW_CUSTOM_STATS='true'), timeout=240)
            watch = run('watch.yml')
            self.assertEqual(watch.returncode, 0, watch.stdout + watch.stderr)
            self.assertEqual(state['polls'], 120)
            self.assertIn('"source_revision": "' + 'c' * 40 + '"', watch.stdout)
            self.assertNotIn('c' * 80, watch.stdout, 'Repeated stats must replace, not concatenate, source identities')
            merged = run('wait-merge.yml')
            self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
            self.assertEqual(state['merged_reads'], 2)
            state.update(drift=True, merged_reads=0)
            drift = run('wait-merge.yml')
            self.assertNotEqual(drift.returncode, 0)
            self.assertEqual(state['merged_reads'], 1, 'Source drift must stop rather than poll indefinitely')
            values.update(ci_watch_attempts=1)
            state['polls'] = 0
            expired = run('watch.yml')
            self.assertNotEqual(expired.returncode, 0)
            self.assertIn('watch bound', expired.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
