"""Exercise real routing, approval guards and ticket upserts against isolated APIs."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
import yaml

ROOT = Path(__file__).resolve().parents[1]

class CVEResponseTests(unittest.TestCase):
    def setUp(self):
        profile = {'application': {'id': 'app', 'service': 'invoice-portal', 'inventory_group': 'pythonapps', 'repository': 'owner/app'},
            'package': {'name': 'example'}, 'advisories': [{'id': 'CVE-TEST', 'cvss_score': 8.0}],
            'response': {'containment': 'stop_service', 'high_severity_threshold': 7.0, 'review_in_days': 7}}
        self.material = {'profile': profile, 'lightwell': {}}
        digest = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        finding = {'schema_version': 1, 'incident_key': 'a' * 64, 'profile_sha256': digest,
            'hosts': ['python1'], 'fix_available': False,
            'observations': [{'host': 'python1', 'profile_sha256': digest, 'assessment_state': 'affected', 'installed_version': '1.0'}]}
        def record(name, artifacts):
            return {'status': 'successful', 'summary_fields': {'job_template': {'name': name}, 'project': {'name': 'demojam-ansible'}}, 'artifacts': artifacts}
        self.jobs = {7: record('invoice_check', {'invoice_findings': finding}),
            8: record('invoice_discover', {'remediation_candidates': [], 'remediation_material_sha256': digest})}
        self.issues = []
        self.writes = []
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                path = urlsplit(self.path)
                if '/projects/' in path.path:
                    return self.reply(200, {'count': 1, 'results': [{'scm_revision': 'd' * 40}]})
                if '/jobs/' in path.path:
                    return self.reply(200, fixture.jobs[int(path.path.rstrip('/').split('/')[-1])])
                page = int(parse_qs(path.query)['page'][0])
                self.reply(200, fixture.issues[(page-1)*50:page*50])
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                issue = dict(value, number=11, state='open')
                fixture.issues.append(issue); fixture.writes.append(('POST', value)); self.reply(201, issue)
            def do_PATCH(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.issues[0].update(value); fixture.writes.append(('PATCH', value)); self.reply(200, fixture.issues[0])
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def run_play(self, filename, extra=None, passes=True):
        with tempfile.TemporaryDirectory() as tmp:
            material = Path(tmp) / 'material.json'; material.write_text(json.dumps(self.material))
            output = Path(tmp) / 'result.json'
            play = yaml.safe_load((ROOT / 'playbooks/invoice' / filename).read_text())[0]
            play['vars_files'] = [str(ROOT / 'playbooks/invoice/vars/orchestration.yml')]
            for task in play['tasks']:
                for key in ['ansible.builtin.import_tasks', 'ansible.builtin.include_tasks']:
                    if key in task: task[key] = str(ROOT / 'playbooks/invoice' / task[key])
            play['tasks'].append({'name': 'Save fixture evidence', 'ansible.builtin.copy': {'dest': str(output),
                'content': '{{ invoice_response_contract | default(invoice_trusted_response) | to_json }}', 'mode': '0600'}})
            path = Path(tmp) / 'play.yml'; path.write_text(yaml.safe_dump([play]))
            variables = {'invoice_assessment_job_id': 7, 'invoice_discovery_job_id': 8,
                'invoice_router_job_id': 9, 'invoice_review_api': f'http://127.0.0.1:{self.server.server_port}',
                'invoice_issue_url': f'http://127.0.0.1:{self.server.server_port}/issues', **(extra or {})}
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path), '-e', json.dumps(variables)],
                capture_output=True, text=True, timeout=90, env=dict(os.environ,
                INVOICE_MATERIAL_FILE=str(material), AAP_USERNAME='fixture', AAP_PASSWORD='fixture-secret', FORGEJO_API_TOKEN='fixture-token'))
            self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
            self.assertNotIn('fixture-secret', result.stdout + result.stderr)
            self.assertNotIn('fixture-token', result.stdout + result.stderr)
            return json.loads(output.read_text()) if passes else None

    def prepare_route(self):
        response = self.run_play('route.yml')
        self.jobs[9] = {'status': 'successful', 'summary_fields': {'job_template': {'name': 'invoice_route'},
            'project': {'name': 'demojam-ansible'}}, 'artifacts': {'invoice_response': response}}
        return response

    def test_no_fix_routes_to_containment_and_ticket_cannot_start_authoring(self):
        response = self.prepare_route()
        self.assertEqual(response['route'], 'contain')
        self.run_play('record-response.yml')
        body = self.issues[0]['body']
        self.assertIn('<!-- security-response:', body)
        self.assertNotIn('<!-- remediation:', body)
        self.assertNotIn('state', self.writes[0][1])
        self.assertIn('Review due (UTC):', body)
        self.run_play('prepare-containment.yml', {'invoice_response': response})
        self.run_play('prepare-containment.yml', {'invoice_response': dict(response, hosts=['webapp'])}, passes=False)

    def test_repeat_detection_reuses_ticket_and_preserves_original_review_deadline(self):
        self.prepare_route(); self.run_play('record-response.yml')
        self.issues[0]['body'] = self.issues[0]['body'].replace('Review due (UTC): ', 'Review due (UTC): ORIGINAL-')
        original = next(line for line in self.issues[0]['body'].splitlines() if line.startswith('Review due (UTC):'))
        self.run_play('record-response.yml')
        self.assertEqual([method for method, _ in self.writes], ['POST', 'PATCH'])
        self.assertIn(original, self.issues[0]['body'])

    def test_untrusted_assessment_or_changed_material_is_rejected(self):
        self.jobs[7]['summary_fields']['job_template']['name'] = 'untrusted'
        self.run_play('route.yml', passes=False)
        self.jobs[7]['summary_fields']['job_template']['name'] = 'invoice_check'
        self.material['profile']['response']['high_severity_threshold'] = 9
        self.run_play('route.yml', passes=False)

    def test_router_failure_and_incomplete_host_observations_do_not_fall_back_to_containment(self):
        self.jobs[8]['status'] = 'failed'
        self.run_play('route.yml', passes=False)
        self.jobs[8]['status'] = 'successful'
        self.jobs[7]['artifacts']['invoice_findings']['hosts'].append('missing-host')
        self.run_play('route.yml', passes=False)
