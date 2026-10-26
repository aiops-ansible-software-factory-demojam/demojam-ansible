"""Execute the trusted red/green verdict against real authored Ansible tasks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class WebRegressionTests(unittest.TestCase):
    def test_status_publisher_rejects_incomplete_or_other_source_receipts(self):
        tasks = yaml.safe_load((ROOT / 'playbooks/webapp/publish-regression.yml').read_text())[0]['tasks'][:3]
        good = {'passed': True, 'suite': 'webapp-regression-v1', 'source_revision': 'a' * 40,
            'base_revision': 'b' * 40, 'regression_sha256': 'c' * 64, 'red': True, 'green': True, 'durable': True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);receipt = root/'result.json';play = root/'publish.yml'
            play.write_text(yaml.safe_dump([{'name':'Validate the real operator receipt checks',
                'hosts':'localhost','connection':'local','gather_facts':False,'tasks':tasks}]))
            for field in [None, 'source_revision', 'base_revision', 'red', 'green', 'durable', 'suite']:
                value = dict(good)
                if field in ['red', 'green', 'durable']:value[field] = False
                elif field:value[field] = 'invalid'
                receipt.write_text(json.dumps(value))
                v = {'webapp_candidate_sha':'a'*40,'webapp_base_sha':'b'*40,
                    'webapp_validation_state':'success','webapp_validation_receipt_file':str(receipt)}
                r = subprocess.run(['ansible-playbook','-i','localhost,',str(play),'-e',json.dumps(v)],
                    env=dict(os.environ,FORGEJO_URL='https://forgejo.fixture',FORGEJO_TOKEN='fixture'),
                    text=True,capture_output=True,timeout=60)
                with self.subTest(field=field):self.assertEqual(r.returncode==0,field is None,r.stdout+r.stderr)

    def verdict(self, phase, fixed, passes, infrastructure_error=False):
        original = yaml.safe_load((ROOT / 'playbooks/webapp/regression/verify.yml').read_text())[1]
        tasks = [task for task in original['tasks'] if task['name'] not in [
            'Independently verify durable repaired SELinux policy', 'Write this host trusted regression receipt']]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regression = root / 'regression.yml'
            authored = [{'name': 'Require the incident fix', 'ansible.builtin.assert': {'that': 'fixture_fixed | bool'}}]
            if infrastructure_error:
                authored = [{'name': 'Broken infrastructure', 'ansible.builtin.command': '/bin/false', 'changed_when': False}]
            regression.write_text(yaml.safe_dump(authored))
            play = root / 'play.yml'
            play.write_text(yaml.safe_dump([{'name': 'Exercise the actual trusted regression verdict',
                'hosts': 'localhost', 'connection': 'local', 'gather_facts': False, 'tasks': tasks}]))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(play), '-e',
                json.dumps({'webapp_regression_phase': phase, 'fixture_fixed': fixed})],
                env=dict(os.environ, WEBAPP_AUTHORED_REGRESSION=str(regression)),
                text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)

    def test_same_assertion_is_red_before_and_green_after(self):
        self.verdict('before', False, True)
        self.verdict('after', True, True)

    def test_test_that_already_passes_original_code_is_rejected(self):
        self.verdict('before', True, False)

    def test_repaired_source_failure_is_rejected(self):
        self.verdict('after', False, False)

    def test_command_failure_cannot_be_counted_as_a_red_regression(self):
        self.verdict('before', False, False, infrastructure_error=True)


if __name__ == '__main__':
    unittest.main()
