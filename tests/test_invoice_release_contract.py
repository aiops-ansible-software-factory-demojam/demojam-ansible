"""Run the trusted release and approval assertions against independent receipts."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ReleaseContractTests(unittest.TestCase):
    def setUp(self):
        material = {'profile': {'application': {'inventory_group': 'pythonapps'}},
                    'lightwell': {'sha256': 'b' * 64}, 'signing_key_sha256': 'c' * 64}
        self.values = {
            'invoice_candidate_sha': 'a' * 40, 'invoice_validation_job_id': 7, 'invoice_pr_number': 2, 'invoice_issue_number': 1,
            'invoice_current_material': material,
            'invoice_merged_pr': {'json': {'merged': True, 'head_sha': 'a' * 40}},
            'invoice_validation_ee': {'json': {'image': 'registry.test/ee@sha256:' + 'e' * 64}},
            'invoice_deploy_template': {'json': {'count': 1, 'results': [{'execution_environment': 8, 'ask_scm_branch_on_launch': True}]}},
            'invoice_validation_record': {'json': {'status': 'successful', 'execution_environment': 8,
                'summary_fields': {'job_template': {'name': 'invoice_validate'}, 'project': {'name': 'demojam-ansible'}},
                'extra_vars': json.dumps({'invoice_candidate_sha': 'a' * 40}),
                'artifacts': {'invoice_validation': {'passed': True, 'source_revision': 'a' * 40,
                    'acceptance_suite': 'trusted-invoice-v1', 'backend': 'aws', 'wheel_sha256': 'b' * 64,
                    'profile_sha256': digest(material['profile']), 'material_sha256': digest(material),
                    'release_manifest_sha256': 'd' * 64}}}},
        }
        source = yaml.safe_load((ROOT / 'playbooks/invoice/prepare-release.yml').read_text())[0]['tasks']
        self.tasks = [t for t in source if 'ansible.builtin.assert' in t or t['name'].split(' | ', 1)[-1] == 'Construct the public immutable release contract']

    def run_gate(self, values, passes, tasks=None):
        with tempfile.TemporaryDirectory() as directory:
            play = [{'name': 'Check the actual production gates', 'hosts': 'localhost', 'connection': 'local',
                     'gather_facts': False, 'tasks': tasks or self.tasks}]
            path = Path(directory) / 'gate.yml'
            path.write_text(yaml.safe_dump(play))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path), '-e', json.dumps(values)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)

    def test_exact_reviewed_receipt_passes_without_creating_aap_resources(self):
        self.run_gate(self.values, True)

    def test_shared_material_or_signing_pin_changed_during_approval_is_rejected(self):
        for field in ['lightwell', 'profile', 'signing_key_sha256']:
            with self.subTest(field=field):
                v = copy.deepcopy(self.values)
                if field == 'lightwell': v['invoice_current_material']['lightwell']['sha256'] = 'f' * 64
                if field == 'profile': v['invoice_current_material']['profile']['application']['inventory_group'] = 'webapps'
                if field == 'signing_key_sha256': v['invoice_current_material']['signing_key_sha256'] = 'f' * 64
                self.run_gate(v, False)

    def test_failed_untrusted_or_wrong_source_validation_is_rejected(self):
        for field in ['status', 'template', 'source', 'image']:
            with self.subTest(field=field):
                v = copy.deepcopy(self.values)
                record = v['invoice_validation_record']['json']
                if field == 'status': record['status'] = 'failed'
                if field == 'template': record['summary_fields']['job_template']['name'] = 'invoice_baseline'
                if field == 'source': record['artifacts']['invoice_validation']['source_revision'] = 'f' * 40
                if field == 'image': v['invoice_deploy_template']['json']['results'][0]['execution_environment'] = 99
                self.run_gate(v, False)

    def test_an_existing_contract_cannot_be_replaced(self):
        v = copy.deepcopy(self.values)
        v['invoice_release_contract'] = {'schema_version': 1, 'source_revision': 'f' * 40}
        self.run_gate(v, False)

    def test_native_approval_belongs_to_this_exact_workflow_and_gate_job(self):
        tasks = yaml.safe_load((ROOT / 'playbooks/invoice/tasks/approval-proof.yml').read_text())
        tasks = [t for t in tasks if 'ansible.builtin.uri' not in t
                 and t.get('ansible.builtin.include_tasks') not in ['controller-read.yml', 'tasks/controller-read.yml']]
        contract = {'schema_version': 1, 'source_revision': 'a' * 40}
        v = {'awx_workflow_job_id': 10, 'awx_workflow_job_name': 'invoice_promote', 'awx_job_id': 11,
             'invoice_candidate_sha': 'a' * 40, 'invoice_checked_contract': contract,
             'invoice_approval_workflow': {'json': {'name': 'invoice_promote', 'scm_branch': 'a' * 40,
                'extra_vars': json.dumps({'invoice_release_contract': contract})}},
             'invoice_approval_nodes': {'json': {'count': 4, 'results': [
                 {'identifier': 'production-approval', 'summary_fields': {'job': {'type': 'workflow_approval', 'status': 'successful'}}},
                 {'identifier': 'recheck-release', 'job': 11}]}}}
        self.run_gate(v, True, tasks)
        for field in ['approval', 'source', 'job']:
            bad = copy.deepcopy(v)
            if field == 'approval': bad['invoice_approval_nodes']['json']['results'][0]['summary_fields']['job']['status'] = 'failed'
            if field == 'source': bad['invoice_approval_workflow']['json']['scm_branch'] = 'f' * 40
            if field == 'job': bad['awx_job_id'] = 12
            self.run_gate(bad, False, tasks)


if __name__ == '__main__':
    unittest.main()
