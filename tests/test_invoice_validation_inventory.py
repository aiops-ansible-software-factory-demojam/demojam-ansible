"""Exercise lint inventory isolation under Controller's strict inventory policy."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(os.environ.get('DEMO_ANSIBLE_SOURCE', Path(__file__).resolve().parents[1]))


class InvoiceValidationInventoryTests(unittest.TestCase):
    def test_lint_has_local_inventory_without_changing_machine_test_inventory(self):
        play = yaml.safe_load((ROOT / 'playbooks/invoice/validate.yml').read_text())[0]
        tasks = next(t['block'] for t in play['tasks'] if t['name'] == 'Run independent candidate validation')
        lint = next(t for t in tasks if t['name'] == 'Lint the candidate invoice automation')
        inventory = next(t for t in tasks if t['name'] == 'Write an isolated inventory for syntax checks')
        machine = next(t for t in tasks if t['name'] == 'Exercise every authored Molecule scenario')
        shared = {'PROVISIONER': 'aws', 'INVOICE_SOURCE_COMMIT': 'a' * 40}
        expression = lint['environment'].removeprefix('{{').removesuffix('}}').strip()
        jinja = Environment(undefined=StrictUndefined)
        jinja.filters['combine'] = lambda base, overrides: dict(base, **overrides)
        self.assertNotIn('ANSIBLE_INVENTORY', shared)
        self.assertNotIn('ANSIBLE_INVENTORY', play['vars']['invoice_test_environment'])
        self.assertEqual(machine['environment'], '{{ invoice_test_environment }}')

        with tempfile.TemporaryDirectory() as directory:
            lint_env = jinja.compile_expression(expression)(invoice_test_environment=shared,
                invoice_validation_directory={'path': directory})
            inventory_path = Path(directory) / 'lint-inventory.yml'
            inventory_path.write_text(inventory['ansible.builtin.copy']['content'])
            self.assertEqual(lint_env, dict(shared, ANSIBLE_INVENTORY=str(inventory_path)))
            self.assertEqual(yaml.safe_load(inventory_path.read_text()),
                {'all': {'hosts': {'localhost': {'ansible_connection': 'local'}}}})
            config = Path(directory) / 'ansible.cfg'
            config.write_text('[defaults]\n')
            source = Path(directory) / 'playbook.yml'
            source.write_text('---\n- name: Check isolated syntax\n  hosts: all\n  gather_facts: false\n'
                              '  tasks:\n    - name: Report syntax fixture\n'
                              '      ansible.builtin.debug:\n        msg: fixture\n')
            env = dict(os.environ, ANSIBLE_INVENTORY_UNPARSED_FAILED='True',
                       ANSIBLE_INVENTORY=str(Path(directory) / 'missing-inventory'),
                       ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR='1')
            command = ['ansible-lint', '--offline', str(source)]
            failed = subprocess.run(command, cwd=directory, env=env, text=True,
                                    capture_output=True, timeout=90)
            self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            self.assertIn('No inventory was parsed', failed.stdout + failed.stderr)
            passed = subprocess.run(command, cwd=directory, env=dict(env, **lint_env),
                                    text=True, capture_output=True, timeout=90)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)


if __name__ == '__main__':
    unittest.main()
