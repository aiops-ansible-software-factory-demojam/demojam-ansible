"""Execute native nginx approval and deployment proof assertions with fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]

class WebPromotionTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.contract = {'schema_version': 1, 'repository': 'demo-owner/ansible-collection-demo.webapp',
            'source_revision': self.sha, 'issue_number': 1, 'pr_number': 2, 'hosts': ['localhost']}
        self.launch = {'webapp_candidate_sha': self.sha, 'webapp_issue_number': 1, 'webapp_pr_number': 2}
        self.v = {**self.launch, 'awx_workflow_job_id': 10, 'awx_workflow_job_name': 'webapp_promote', 'awx_job_id': 12,
            'webapp_require_production_approval': True,
            'webapp_merged_pr': {'json': {'merged': True, 'head_sha': self.sha}},
            'webapp_promotion_workflow': {'json': {'name': 'webapp_promote', 'scm_branch': self.sha, 'extra_vars': json.dumps(self.launch)}},
            'webapp_promotion_nodes': {'json': {'count': 4, 'results': [
                {'identifier': 'production-approval', 'summary_fields': {'job': {'type': 'workflow_approval', 'status': 'successful'}}},
                {'identifier': 'recheck-release', 'job': 12}]}}}
        self.review_tasks = [t for t in yaml.safe_load((ROOT / 'playbooks/webapp/review-release.yml').read_text())[0]['tasks']
            if 'ansible.builtin.assert' in t or 'ansible.builtin.set_fact' in t]
    def run_tasks(self, values, tasks, passes=True):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp)/'inventory.yml'; inv.write_text(yaml.safe_dump({'all': {'children': {'webapps': {'hosts': {'localhost': {'ansible_connection': 'local'}}}, 'pythonapps': {'hosts': {'python-host': {}}}}}}))
            play = Path(tmp)/'play.yml'; play.write_text(yaml.safe_dump([{'name': 'Exercise real web promotion guards',
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False, 'tasks': tasks}]))
            r = subprocess.run(['ansible-playbook', '-i', str(inv), str(play), '-e', json.dumps(values)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode == 0, passes, r.stdout + r.stderr)
    def test_review_requires_matching_merge_source_and_approval(self):
        self.run_tasks(self.v, self.review_tasks)
        for field in ['merge', 'source', 'approval', 'job', 'incident', 'contract']:
            v = copy.deepcopy(self.v)
            if field == 'merge': v['webapp_merged_pr']['json']['merged'] = False
            if field == 'source': v['webapp_promotion_workflow']['json']['scm_branch'] = 'b' * 40
            if field == 'approval': v['webapp_promotion_nodes']['json']['results'][0]['summary_fields']['job']['status'] = 'pending'
            if field == 'job': v['awx_job_id'] = 99
            if field == 'incident': v['webapp_issue_number'] = 99
            if field == 'contract': v['webapp_release_contract'] = dict(self.contract, hosts=['python-host'])
            with self.subTest(field=field): self.run_tasks(v, self.review_tasks, False)
    def proof(self):
        guard_records = {'results': [{'item': {'identifier': identifier}, 'json': {'status': 'successful',
            'summary_fields': {'job_template': {'name': 'webapp_review_release'}, 'project': {'name': 'demojam-ansible'}},
            'extra_vars': json.dumps({'webapp_require_production_approval': identifier == 'recheck-release'}),
            'artifacts': {'webapp_release_contract': self.contract}}} for identifier in ['validate-release','recheck-release']]}
        values = {**self.launch,
            'webapp_production_workflow': {'json': {'status': 'successful', 'name': 'webapp_promote', 'scm_branch': self.sha, 'extra_vars': json.dumps(self.launch)}},
            'webapp_production_nodes': {'json': {'count': 4}}, 'webapp_production_guards': [{}, {}],
            'webapp_production_approval': [{'summary_fields': {'job': {'type': 'workflow_approval','status': 'successful'}}}],
            'webapp_production_deploy': [{'summary_fields': {'job': {'status': 'successful'}}}],
            'webapp_production_guard_records': guard_records, 'webapp_production_contract': self.contract,
            'webapp_production_hosts': {'localhost': {'source_revision': self.sha, 'selinux': 'enforcing', 'http_status': 200, 'passed': True}},
            'webapp_production_job': {'json': {'status': 'successful', 'scm_revision': self.sha, 'extra_vars': json.dumps(self.launch),
                'summary_fields': {'job_template': {'name': 'webapp_deploy'}, 'project': {'name': 'webapp-collection'}},
                'artifacts': {'webapp_release_contract': self.contract}}}}
        tasks = [t for t in yaml.safe_load((ROOT / 'playbooks/webapp/verify-deployment.yml').read_text())[0]['tasks']
            if t['name'] in ['Require successful approval and deployment of the exact merged incident',
                'Require trusted successful gate executions around human approval', 'Require pinned source and complete enforcing HTTP recovery']]
        return values, tasks
    def test_complete_production_proof_passes(self):
        self.run_tasks(*self.proof())
    def test_failed_or_incomplete_production_proof_stops(self):
        for field in ['approval', 'source', 'http', 'selinux', 'hosts', 'gate', 'template']:
            v,tasks = copy.deepcopy(self.proof())
            if field == 'approval': v['webapp_production_approval'][0]['summary_fields']['job']['status'] = 'failed'
            if field == 'source': v['webapp_production_job']['json']['scm_revision'] = 'b' * 40
            if field == 'http': v['webapp_production_hosts']['localhost']['http_status'] = 403
            if field == 'selinux': v['webapp_production_hosts']['localhost']['selinux'] = 'permissive'
            if field == 'hosts': v['webapp_production_hosts'] = {}
            if field == 'gate': v['webapp_production_guard_records']['results'][1]['json']['extra_vars'] = '{}'
            if field == 'template': v['webapp_production_job']['json']['summary_fields']['job_template']['name'] = 'webapp_nginx'
            with self.subTest(field=field): self.run_tasks(v, tasks, False)
    def test_platform_graph_has_only_approved_rechecked_deploy_path(self):
        w = yaml.safe_load((ROOT/'group_vars/aap/workflows.yml').read_text())['controller_workflows'][0]
        nodes = {n['identifier']: n for n in w['simplified_workflow_nodes']}
        self.assertEqual(w['name'], 'webapp_promote'); self.assertTrue(w['ask_scm_branch_on_launch'])
        self.assertEqual(nodes['validate-release']['success_nodes'], ['production-approval'])
        self.assertEqual(nodes['production-approval']['success_nodes'], ['recheck-release'])
        self.assertTrue(nodes['recheck-release']['extra_data']['webapp_require_production_approval'])
        self.assertEqual(nodes['recheck-release']['success_nodes'], ['deploy'])
        for node in nodes.values():
            self.assertFalse(node.get('always_nodes')); self.assertFalse(node.get('failure_nodes'))
        jt = next(t for t in yaml.safe_load((ROOT/'group_vars/aap/job_templates.yml').read_text())['demo_controller_templates'] if t['name']=='webapp_deploy')
        self.assertEqual(jt['credentials'], ['demo-webapp-ssh']); self.assertTrue(jt['ask_scm_branch_on_launch'])
