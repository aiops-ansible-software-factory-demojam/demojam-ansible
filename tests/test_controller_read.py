"""Exercise shared Controller reads, including check mode and failed authentication."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ControllerReadTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fixture.requests.append((self.path, self.headers.get('Authorization')))
                wanted = 'Basic ' + base64.b64encode(b'reviewer:PRIVATE-REVIEW-PASSWORD').decode()
                status = 401 if self.headers.get('Authorization') != wanted else 200
                if self.path == '/missing/':
                    status = 404
                body = json.dumps({'path': self.path, 'marker': 'PRIVATE-CONTROLLER-RESPONSE'}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.worker.join)
        self.addCleanup(self.server.shutdown)

    def run_reads(self, *, check=False, password='PRIVATE-REVIEW-PASSWORD', path='jobs/7/', passes=True):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            output = tmp / 'records.json'
            tasks = []
            for name, endpoint in [('invoice_first', path), ('invoice_second', 'projects/?name=invoice-collection&organization__name=demo')]:
                tasks.append({'name': 'Read an independent fixture record', 'ansible.builtin.include_tasks':
                    str(ROOT / 'playbooks/invoice/tasks/controller-read.yml'), 'vars': {
                        'controller_read_path': endpoint, 'controller_read_register': name}})
            tasks.append({'name': 'Save fixture records for assertions', 'ansible.builtin.copy': {
                'dest': str(output), 'content': '{{ [invoice_first.json, invoice_second.json] | to_json }}',
                'mode': '0600'}, 'check_mode': False})
            play = [{'name': 'Exercise shared Controller transport', 'hosts': 'localhost', 'connection': 'local',
                     'gather_facts': False, 'tasks': tasks, 'vars': {
                         'invoice_review_api': f'http://127.0.0.1:{self.server.server_port}',
                         'invoice_review_username': 'reviewer', 'invoice_review_password': password}}]
            playbook = tmp / 'read.json'
            playbook.write_text(json.dumps(play))
            command = ['ansible-playbook', '-i', 'localhost,', str(playbook)]
            if check:
                command.append('--check')
            result = subprocess.run(command, text=True, capture_output=True, timeout=60,
                                    env=dict(os.environ, ANSIBLE_NOCOLOR='1'))
            self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
            self.assertNotIn('PRIVATE-REVIEW-PASSWORD', result.stdout + result.stderr)
            self.assertNotIn('PRIVATE-CONTROLLER-RESPONSE', result.stdout + result.stderr)
            return json.loads(output.read_text()) if passes else None

    def test_sequential_reads_keep_distinct_records_and_query_parameters(self):
        records = self.run_reads()
        self.assertEqual(records[0]['path'], '/jobs/7/')
        self.assertEqual(records[1]['path'], '/projects/?name=invoice-collection&organization__name=demo')
        self.assertEqual(len(self.requests), 2)

    def test_check_mode_still_reads_authoritative_evidence(self):
        self.assertEqual(self.run_reads(check=True)[0]['path'], '/jobs/7/')
        self.assertEqual(len(self.requests), 2)

    def test_denied_credentials_or_missing_evidence_stops_before_next_read(self):
        for extra in [{'password': 'wrong'}, {'path': 'missing/'}]:
            with self.subTest(extra=extra):
                self.requests.clear()
                self.run_reads(passes=False, **extra)
                self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
