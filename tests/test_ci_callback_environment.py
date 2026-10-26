"""Nested tests must use readable callbacks, independently of Controller's callback."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CallbackEnvironmentTests(unittest.TestCase):
    def test_real_child_playbook_ignores_parent_callback(self):
        web = yaml.safe_load((ROOT / 'playbooks/ci/webapp.yml').read_text())[0]
        fact = next(t['ansible.builtin.set_fact']['ci_environment'] for t in web['tasks']
                    if t['name'] == 'Read the operator-selected disposable test environment')
        jinja = Environment(undefined=StrictUndefined)
        jinja.filters.update(from_json=json.loads, combine=lambda a, b: dict(a, **b))
        jinja.globals['lookup'] = lambda kind, value: '/fixture/profile' if kind == 'ansible.builtin.env' else '{}'
        shared = jinja.compile_expression(fact.removeprefix('{{').removesuffix('}}').strip())(ci_directory={'path': '/fixture'})
        invoice = yaml.safe_load((ROOT / 'playbooks/invoice/validate.yml').read_text())[0]['vars']['invoice_test_environment']
        with tempfile.TemporaryDirectory() as directory:
            play = Path(directory) / 'readable.yml'
            play.write_text('---\n- name: Nested test\n  hosts: localhost\n  gather_facts: false\n'
                            '  tasks:\n    - name: Readable task\n      ansible.builtin.debug:\n        msg: child-test-output\n')
            command = ['ansible-playbook', '-i', 'localhost,', str(play)]
            parent = dict(os.environ, ANSIBLE_STDOUT_CALLBACK='missing_controller_callback')
            failed = subprocess.run(command, env=parent, text=True, capture_output=True, timeout=30)
            self.assertNotEqual(failed.returncode, 0)
            for environment in [shared, invoice]:
                with self.subTest(environment=environment['ANSIBLE_STDOUT_CALLBACK']):
                    overrides = {key: environment[key] for key in ['ANSIBLE_STDOUT_CALLBACK', 'ANSIBLE_FORCE_COLOR']}
                    child = subprocess.run(command, env=dict(parent, **overrides), text=True, capture_output=True, timeout=30)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
                    self.assertIn('TASK [Readable task]', child.stdout)
                    self.assertIn('child-test-output', child.stdout)
