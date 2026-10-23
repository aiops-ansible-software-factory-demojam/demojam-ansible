"""Run the actual Ansible reconciliation against a local paginated Forgejo fixture."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
KEY = 'a' * 64
MARKER = '<!-- remediation:' + KEY + ' -->'


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.issues = []
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                page = int(parse_qs(urlsplit(self.path).query)['page'][0])
                fixture.requests.append(('GET', page))
                self.reply(200, fixture.issues[(page - 1) * 50:page * 50])

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(('POST', body))
                self.reply(201, dict(body, number=101, html_url='https://fixture.test/issues/101'))

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(('PATCH', body))
                self.reply(200, body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_reconciliation(self, affected=True, fix=True, state='affected', success=True, business_passed=True):
        finding = {'schema_version': 1, 'incident_key': KEY, 'affected': affected, 'fix_available': fix,
                   'assessment_state': state, 'business_passed': business_passed, 'application': 'fixture-app', 'environment': 'test',
                   'application_profile': {'repository': 'owner/app', 'release_manifest': 'releases/app.json'},
                   'package': 'example-package', 'installed_version': '1.1' if state == 'fixed' else '1.0', 'advisories': ['TEST-1'],
                   'advisory_evidence': [{'id': 'TEST-1', 'fixed_versions': ['1.1']}],
                   'validation': {'scenario': 'application'}}
        with tempfile.TemporaryDirectory() as directory:
            variables = Path(directory) / 'vars.json'
            variables.write_text(json.dumps({'invoice_findings': finding,
                'invoice_issue_url': f'http://127.0.0.1:{self.server.server_port}/issues'}))
            result = subprocess.run([shutil.which('ansible-playbook'), '-i', 'localhost,',
                '-e', '@' + str(variables), str(ROOT / 'playbooks/invoice/open-issue.yml')],
                capture_output=True, text=True, timeout=60,
                env=dict(os.environ, FORGEJO_API_TOKEN='fixture-token'))
            self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
            self.assertNotIn('fixture-token', result.stdout + result.stderr)

    def test_existing_incident_on_second_page_is_updated_without_duplicate(self):
        self.issues = [{'number': n, 'state': 'closed', 'body': 'unrelated'} for n in range(50)]
        self.issues.append({'number': 51, 'state': 'open', 'body': MARKER, 'html_url': 'https://fixture.test/51'})
        self.run_reconciliation()
        self.assertEqual([r[1] for r in self.requests if r[0] == 'GET'], [1, 2])
        self.assertEqual(len([r for r in self.requests if r[0] == 'POST']), 0)
        self.assertEqual(len([r for r in self.requests if r[0] == 'PATCH']), 1)

    def test_recurrence_after_closed_incident_creates_new_ticket(self):
        self.issues = [{'number': 1, 'state': 'closed', 'body': MARKER}]
        self.run_reconciliation()
        self.assertEqual(len([r for r in self.requests if r[0] == 'POST']), 1)

    def test_no_supported_fix_does_not_dispatch_authoring(self):
        self.run_reconciliation(fix=False)
        self.assertFalse(any(r[0] == 'POST' for r in self.requests))

    def test_observed_fixed_version_closes_open_incident(self):
        self.issues = [{'number': 1, 'state': 'open', 'body': MARKER, 'html_url': 'https://fixture.test/1'}]
        self.run_reconciliation(affected=False, fix=False, state='fixed')
        self.assertIn(('PATCH', {'state': 'closed'}), self.requests)

    def test_verified_fix_also_closes_the_temporary_response_ticket(self):
        self.issues = [{'number': 1, 'state': 'open', 'body': MARKER},
                       {'number': 2, 'state': 'open', 'body': '<!-- security-response:' + KEY + ' -->'}]
        self.run_reconciliation(affected=False, fix=False, state='fixed')
        self.assertEqual(len([r for r in self.requests if r == ('PATCH', {'state': 'closed'})]), 2)

    def test_stopped_or_unhealthy_fixed_service_does_not_close_incident(self):
        self.issues = [{'number': 1, 'state': 'open', 'body': MARKER}]
        self.run_reconciliation(affected=False, fix=False, state='fixed', business_passed=False)
        self.assertFalse(any(r[0] == 'PATCH' for r in self.requests))

    def test_unknown_version_does_not_resolve_incident(self):
        self.issues = [{'number': 1, 'state': 'open', 'body': MARKER, 'html_url': 'https://fixture.test/1'}]
        self.run_reconciliation(affected=False, fix=False, state='unknown')
        self.assertFalse(any(r[0] == 'PATCH' for r in self.requests))


if __name__ == '__main__':
    unittest.main()
