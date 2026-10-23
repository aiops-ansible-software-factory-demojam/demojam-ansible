"""Check credential serialization with the supported EE's Ansible templater."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import init_plugin_loader
from ansible.template import Templar
import yaml


class InventoryLoader(yaml.SafeLoader):
    pass


InventoryLoader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))
ROOT = Path(__file__).resolve().parents[1]
init_plugin_loader()


class InvoiceCredentialTests(unittest.TestCase):
    def setUp(self):
        self.material = {"lightwell": {"sha256": "a" * 64}, "repository_token": "fixture"}
        fields = ["region", "deployment_id", "vpc_id", "subnet_id", "molecule_security_group_id",
                  "molecule_instance_type", "rhel9_ami_id", "rhel10_ami_id", "ssh_cidr", "http_cidr"]
        config = {name: "fixture" for name in fields}
        config.update(account_id="123456789012", root_volume_size=30)
        self.aws = {"access_key_id": "fixture", "secret_access_key": "fixture", "config": config}
        self.environment = patch.dict(os.environ, {
            "DEMO_INVOICE_MATERIAL": json.dumps(self.material),
            "DEMO_INVOICE_TEST_AWS": json.dumps(self.aws),
            "DEMO_INVOICE_SIGNING_KEY": "fixture-public-key",
            "INVOICE_TEST_EE_IMAGE": "registry.example/test@sha256:" + "b" * 64,
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        variables = yaml.safe_load((ROOT / "vars/invoice/factory.yml").read_text())
        self.templater = Templar(loader=DataLoader(), variables=variables)
        play = yaml.load((ROOT / "playbooks/aap/configure-invoice.yml").read_text(), Loader=InventoryLoader)[0]
        self.tasks = play["tasks"]
        self.platform = yaml.load((ROOT / "vars/invoice/platform.yml").read_text(), Loader=InventoryLoader)

    def test_artifact_credential_contains_json_text(self):
        credential = next(c for c in self.platform["invoice_platform_controller_credentials"] if c["name"] == "demo-invoice-artifacts")
        material = self.templater.template(credential["inputs"]["material"])
        self.assertIsInstance(material, str)
        self.assertEqual(json.loads(material), self.material)

    def test_all_test_identity_fields_remain_strings(self):
        credential = next(c for c in self.platform["invoice_platform_controller_credentials"] if c["name"] == "demo-invoice-tests")
        inputs = self.templater.template(credential["inputs"])
        for name, value in inputs.items():
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
        self.assertEqual(inputs["account_id"], "123456789012")
        self.assertEqual(inputs["root_volume_size"], "30")

    def test_release_resources_are_reused_instead_of_generated_per_commit(self):
        self.assertFalse((ROOT / 'vars/invoice/release.yml').exists())
        names = [t['name'] for t in self.platform['invoice_platform_controller_templates']]
        self.assertEqual(names.count('invoice_deploy'), 1)
        self.assertEqual(names.count('invoice_prepare_release'), 1)
        self.assertEqual(len(self.platform['invoice_platform_controller_projects']), 1)
        self.assertEqual(len(self.platform['invoice_platform_controller_workflows']), 2)
        self.assertFalse(any('invoice_candidate_sha' in n for n in names))

    def test_only_approved_rechecked_workflow_path_reaches_deployment(self):
        workflow = self.platform['invoice_platform_controller_workflows'][0]
        self.assertEqual(workflow['name'], 'invoice_promote')
        self.assertTrue(workflow['ask_variables_on_launch'])
        self.assertTrue(workflow['ask_scm_branch_on_launch'])
        self.assertFalse(workflow['allow_simultaneous'])
        nodes = {n['identifier']: n for n in workflow['simplified_workflow_nodes']}
        self.assertEqual(len(nodes), 4)
        self.assertEqual(nodes['validate-release']['success_nodes'], ['production-approval'])
        self.assertEqual(nodes['production-approval']['approval_node']['timeout'], 1800)
        self.assertEqual(nodes['production-approval']['success_nodes'], ['recheck-release'])
        self.assertEqual(nodes['recheck-release']['success_nodes'], ['deploy'])
        self.assertTrue(nodes['recheck-release']['extra_data']['invoice_require_production_approval'])
        for node in nodes.values():
            self.assertFalse(node.get('always_nodes'))
            self.assertFalse(node.get('failure_nodes'))
        deployment = next(t for t in self.platform['invoice_platform_controller_templates'] if t['name'] == 'invoice_deploy')
        self.assertTrue(deployment['ask_scm_branch_on_launch'])
        self.assertTrue(deployment['ask_variables_on_launch'])
        self.assertEqual(deployment['project'], 'invoice-collection')
        self.assertNotIn('demo-invoice-review-api', deployment['credentials'])
        self.assertTrue(self.platform['invoice_platform_controller_projects'][0]['allow_override'])

    def test_containment_has_a_separate_approval_and_trusted_recheck(self):
        workflow = next(w for w in self.platform['invoice_platform_controller_workflows'] if w['name'] == 'invoice_contain')
        nodes = {n['identifier']: n for n in workflow['simplified_workflow_nodes']}
        self.assertEqual(nodes['validate-request']['success_nodes'], ['containment-approval'])
        self.assertEqual(nodes['containment-approval']['success_nodes'], ['recheck-request'])
        self.assertTrue(nodes['recheck-request']['extra_data']['invoice_require_containment_approval'])
        self.assertEqual(nodes['recheck-request']['success_nodes'], ['contain'])
        for node in nodes.values():
            self.assertFalse(node.get('always_nodes'))
            self.assertFalse(node.get('failure_nodes'))
        containment = next(t for t in self.platform['invoice_platform_controller_templates'] if t['name'] == 'invoice_contain_service')
        self.assertNotIn('demo-invoice-review-api', containment['credentials'])

    def test_dispatch_only_owns_invoice_resources_in_dependency_order(self):
        dispatch = next(t for t in self.tasks if "ansible.builtin.include_role" in t)
        self.assertEqual(dispatch["ansible.builtin.include_role"]["name"], "infra.aap_configuration.dispatch")
        roles = dispatch["vars"]["controller_configuration_dispatcher_roles"]
        self.assertEqual([r["var"] for r in roles], ["controller_credential_types", "controller_credentials",
                         "controller_execution_environments", "controller_projects", "controller_templates", "controller_workflows"])
        self.assertEqual(dispatch["vars"]["gateway_configuration_dispatcher_roles"], [])
        self.assertEqual(dispatch["vars"]["hub_configuration_dispatcher_roles"], [])


if __name__ == "__main__":
    unittest.main()
