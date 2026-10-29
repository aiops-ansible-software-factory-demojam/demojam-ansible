"""Check inventory discovery, sidecar vars and backend source selection without APIs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class InventoryLayoutTests(unittest.TestCase):
    def inventory(self, *extra):
        result = subprocess.run(['ansible-inventory', '--list', *extra], cwd=ROOT,
            env=dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg')),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_default_inventory_loads_static_hosts_and_aap_group_vars(self):
        inventory = self.inventory()
        self.assertEqual(inventory['aap']['hosts'], ['aap_demo'])
        self.assertEqual(inventory['openshift_clusters']['hosts'], ['demo_cluster'])
        variables = inventory['_meta']['hostvars']
        self.assertEqual(variables['aap_demo']['demo_controller_inventory_sources'][0]['source_path'], 'inventory/hosts.yml')
        self.assertEqual(variables['demo_cluster']['vm_namespace'], 'automation-vms')
        self.assertNotIn('invoice_factory_material', variables['aap_demo'])

    def test_dynamic_host_groups_still_find_their_sidecar_variables(self):
        with tempfile.TemporaryDirectory() as directory:
            overlay = Path(directory) / 'hosts.yml'
            overlay.write_text(yaml.safe_dump({'webapps': {'hosts': {'fixture_web': {}}},
                                               'pythonapps': {'hosts': {'fixture_python': {}}}}))
            inventory = self.inventory('-i', str(ROOT / 'inventory/hosts.yml'), '-i', str(overlay))
            variables = inventory['_meta']['hostvars']
            self.assertIn('get_service_status', variables['fixture_web']['webapp_mcp_tools'])
            self.assertEqual(variables['fixture_python']['ansible_user'], 'ec2-user')

    def test_controller_selects_explicit_sources_for_only_the_requested_backend(self):
        for backend, expected in [
            ('cnv', ['inventory/hosts.yml', 'inventory/openshift-virtualization.kubevirt.yml']),
            ('aws', ['inventory/hosts.yml', 'inventory/webapp.aws_ec2.yml', 'inventory/pythonapp.aws_ec2.yml']),
        ]:
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                for path in expected:
                    self.assertTrue((ROOT / path).is_file())
                    self.assertEqual((ROOT / path).parent, ROOT / 'inventory')
                play = [{'name': 'Check actual AAP inventory source routing', 'hosts': 'localhost',
                    'connection': 'local', 'gather_facts': False,
                    'vars_files': [str(ROOT / 'inventory/group_vars/aap/inventories.yml')],
                    'vars': {'demo_backend': backend, 'expected_paths': expected},
                    'tasks': [{'name': 'Require explicit backend-specific source files',
                        'ansible.builtin.assert': {'that':
                            "controller_inventory_sources | map(attribute='source_path') | list == expected_paths"}}]}]
                path = Path(directory) / 'check.json'
                path.write_text(json.dumps(play))
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
