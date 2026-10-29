"""Exercise acceptance source resolution with Ansible's real variable precedence."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
COLLECTION = Path(os.environ.get(
    'DEMO_PYTHONAPP_SOURCE', ROOT.parent / 'ansible-collection-demo.pythonapp'))


class AcceptanceSourceTests(unittest.TestCase):
    def test_baseline_profile_resolves_candidate_assets_from_an_external_playbook(self):
        original = yaml.safe_load((ROOT / 'playbooks/invoice/acceptance/molecule/fault-prepare.yml').read_text())[0]
        deployment = original['tasks'][0]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            role = directory / 'roles/source_contract/tasks'
            role.mkdir(parents=True)
            (role.parent / 'vars').mkdir()
            (role.parent / 'vars/invoice.yml').write_text((COLLECTION / 'roles/python_application/vars/invoice.yml').read_text())
            (role / 'main.yml').write_text(yaml.safe_dump([
                {'name': 'Require the reviewed collection source directory',
                 'ansible.builtin.assert': {'that':
                     'python_application_source | realpath == expected_application_source'}},
                {'name': 'Require the real application source to be readable',
                 'ansible.builtin.stat': {'path': '{{ python_application_source }}/portal.py'},
                 'register': 'application_source'},
                {'name': 'Require the source file',
                 'ansible.builtin.assert': {'that': 'application_source.stat.isreg'}},
            ]))
            env = dict(os.environ,
                REMEDIATION_CANDIDATE_PLAYBOOK=str(COLLECTION / 'playbooks/invoice-remediate.yml'),
                INVOICE_SOURCE_COMMIT='a' * 40,
                ANSIBLE_INVENTORY_UNPARSED_FAILED='False')
            for redirected, override in [(False, False), (False, True), (True, False), (True, True)]:
                with self.subTest(redirected_profile=redirected, task_override=override):
                    profile = yaml.safe_load((COLLECTION / 'roles/python_application/vars/invoice.yml').read_text())
                    if redirected:
                        profile['python_application_source'] = str(directory / 'unreviewed-source')
                    (role.parent / 'vars/invoice.yml').write_text(yaml.safe_dump(profile))
                    task = copy.deepcopy(deployment)
                    task['ansible.builtin.include_role']['name'] = 'source_contract'
                    if not override:
                        task.pop('vars', None)
                    play = {
                        'name': 'Exercise the independent baseline source contract',
                        'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                        'vars': dict(original['vars'],
                            expected_application_source=str(COLLECTION / 'roles/invoice/files/invoice')),
                        'vars_files': original['vars_files'], 'tasks': [task],
                    }
                    path = directory / 'acceptance.yml'
                    path.write_text(yaml.safe_dump([play]))
                    result = subprocess.run(
                        ['ansible-playbook', '-i', 'localhost,', str(path)],
                        cwd=directory, env=env, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode == 0, not redirected or override,
                                     result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
