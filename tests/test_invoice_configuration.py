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
        self.release = yaml.load((ROOT / "vars/invoice/release.yml").read_text(), Loader=InventoryLoader)

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

    def test_release_snapshot_uses_the_same_json_boundary(self):
        credential = self.release["invoice_release_controller_credentials"][0]
        material = self.templater.template(credential["inputs"]["material"])
        self.assertIsInstance(material, str)
        self.assertEqual(json.loads(material), self.material)

    def test_only_approved_workflow_path_reaches_pinned_deployment(self):
        workflow = self.release["invoice_release_controller_workflows"][0]
        nodes = workflow["simplified_workflow_nodes"]
        approval = next(n for n in nodes if "approval_node" in n)
        self.assertEqual(approval["identifier"], "production-approval")
        self.assertEqual(approval["approval_node"]["timeout"], 1800)
        self.assertEqual(approval["success_nodes"], ["deploy"])
        self.assertFalse(approval.get("always_nodes"))
        self.assertFalse(approval.get("failure_nodes"))
        self.assertEqual(len(nodes), 2)
        deployment = self.release["invoice_release_controller_templates"][0]
        self.assertFalse(deployment["ask_scm_branch_on_launch"])
        self.assertFalse(deployment["ask_variables_on_launch"])
        project = self.release["invoice_release_controller_projects"][0]
        self.assertEqual(project["scm_branch"], "{{ invoice_candidate_sha }}")
        self.assertFalse(project["allow_override"])

    def test_dispatch_only_owns_invoice_resources_in_dependency_order(self):
        dispatch = next(t for t in self.tasks if "ansible.builtin.include_role" in t)
        self.assertEqual(dispatch["ansible.builtin.include_role"]["name"], "infra.aap_configuration.dispatch")
        roles = dispatch["vars"]["controller_configuration_dispatcher_roles"]
        self.assertEqual([r["var"] for r in roles], ["controller_credential_types", "controller_credentials",
                         "controller_execution_environments", "controller_projects", "controller_templates"])
        self.assertEqual(dispatch["vars"]["gateway_configuration_dispatcher_roles"], [])
        self.assertEqual(dispatch["vars"]["hub_configuration_dispatcher_roles"], [])


if __name__ == "__main__":
    unittest.main()
