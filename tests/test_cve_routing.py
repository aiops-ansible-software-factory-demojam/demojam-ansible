"""Execute the actual Ansible routing policy and approval assertions."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

class CVERoutingTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            'invoice_response_material': {'profile': {'package': {'name': 'example'},
                'advisories': [{'id': 'CVE-TEST', 'cvss_score': 8.0}],
                'response': {'containment': 'stop_service', 'high_severity_threshold': 7.0, 'review_in_days': 30}}},
            'invoice_routing_finding': {'fix_available': False, 'observations': [{'assessment_state': 'affected'}]},
            'invoice_response_fix_confirmed': False}
        self.tasks = yaml.safe_load((ROOT / 'playbooks/invoice/tasks/route-decision.yml').read_text())

    def route(self, values, expected=None, passes=True, tasks=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'result.json'
            steps = tasks if tasks is not None else self.tasks + [{
                'name': 'Save the actual route for this fixture', 'ansible.builtin.copy': {
                    'content': '{{ {"route": invoice_response_route | trim} | to_json }}', 'dest': str(out), 'mode': '0600'}}]
            play = Path(tmp) / 'gate.yml'
            play.write_text(yaml.safe_dump([{'name': 'Exercise actual CVE response policy', 'hosts': 'localhost',
                'connection': 'local', 'gather_facts': False, 'tasks': steps}]))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(play), '-e', json.dumps(values)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
            if expected is not None:
                self.assertEqual(json.loads(out.read_text())['route'], expected)

    def test_approved_discovered_fix_uses_existing_application_path(self):
        self.values['invoice_routing_finding']['fix_available'] = True
        self.values['invoice_response_fix_confirmed'] = True
        self.route(self.values, 'app_patch')

    def test_high_severity_without_fix_requires_containment(self):
        self.route(self.values, 'contain')

    def test_low_severity_without_fix_is_deferred(self):
        self.values['invoice_response_material']['profile']['advisories'][0]['cvss_score'] = 6.2
        self.route(self.values, 'log_only')

    def test_unknown_version_missing_score_or_unsupported_ecosystem_needs_review(self):
        for field in ['version', 'score', 'ecosystem', 'control', 'mixed']:
            v = copy.deepcopy(self.values)
            if field == 'version': v['invoice_routing_finding']['observations'][0]['assessment_state'] = 'unknown'
            if field == 'score': v['invoice_response_material']['profile']['advisories'][0].pop('cvss_score')
            if field == 'ecosystem': v['invoice_response_material']['profile']['package']['ecosystem'] = 'rpm'
            if field == 'control': v['invoice_response_material']['profile']['response']['containment'] = 'manual'
            if field == 'mixed': v['invoice_routing_finding']['observations'].append({'assessment_state': 'fixed'})
            with self.subTest(field=field): self.route(v, 'manual_review')

    def test_fixed_hosts_do_not_trigger_a_change(self):
        self.values['invoice_routing_finding']['observations'][0]['assessment_state'] = 'fixed'
        self.route(self.values, 'log_only')

    def test_catalog_availability_without_prepared_approval_is_not_a_patch(self):
        self.values['invoice_routing_finding']['fix_available'] = True
        self.route(self.values, 'contain')

    def test_invalid_severity_or_policy_is_rejected(self):
        for score in [-1, 11, 'critical']:
            v = copy.deepcopy(self.values)
            v['invoice_response_material']['profile']['advisories'][0]['cvss_score'] = score
            with self.subTest(score=score): self.route(v, passes=False)

    def test_containment_approval_must_match_this_request_and_gate(self):
        tasks = [t for t in yaml.safe_load((ROOT / 'playbooks/invoice/tasks/containment-approval.yml').read_text())
                 if 'ansible.builtin.uri' not in t
                 and t.get('ansible.builtin.include_tasks') not in ['controller-read.yml', 'tasks/controller-read.yml']]
        response = {'route': 'contain', 'hosts': ['python1'], 'source_revision': 'd' * 40}
        values = {'awx_workflow_job_id': 9, 'awx_workflow_job_name': 'invoice_contain', 'awx_job_id': 12,
            'invoice_router_job_id': 7, 'invoice_trusted_response': response,
            'invoice_containment_workflow': {'json': {'name': 'invoice_contain', 'scm_branch': 'd' * 40, 'extra_vars': json.dumps({
                'invoice_response': response, 'invoice_router_job_id': 7})}},
            'invoice_containment_nodes': {'json': {'count': 4, 'results': [
                {'identifier': 'containment-approval', 'summary_fields': {'job': {'type': 'workflow_approval', 'status': 'successful'}}},
                {'identifier': 'recheck-request', 'job': 12}]}}}
        self.route(values, tasks=tasks)
        for field in ['approval', 'job', 'request', 'source']:
            bad = copy.deepcopy(values)
            if field == 'source': bad['invoice_containment_workflow']['json']['scm_branch'] = 'e' * 40
            if field == 'approval': bad['invoice_containment_nodes']['json']['results'][0]['summary_fields']['job']['status'] = 'pending'
            if field == 'job': bad['awx_job_id'] = 13
            if field == 'request': bad['invoice_trusted_response']['hosts'] = ['another-host']
            with self.subTest(field=field): self.route(bad, passes=False, tasks=tasks)
