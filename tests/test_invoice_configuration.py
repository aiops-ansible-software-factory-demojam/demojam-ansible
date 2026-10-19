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
        variables = yaml.safe_load((ROOT / "group_vars/invoice_factory/main.yml").read_text())
        self.templater = Templar(loader=DataLoader(), variables=variables)
        play = yaml.load((ROOT / "playbooks/aap/configure-invoice.yml").read_text(), Loader=InventoryLoader)[0]
        self.tasks = play["tasks"]

    def test_artifact_credential_contains_json_text(self):
        task = next(t for t in self.tasks if t["name"] == "Register the artifact read credential")
        material = self.templater.template(task["ansible.controller.credential"]["inputs"]["material"])
        self.assertIsInstance(material, str)
        self.assertEqual(json.loads(material), self.material)

    def test_all_test_identity_fields_remain_strings(self):
        task = next(t for t in self.tasks if t["name"] == "Register the scoped test identity without production credentials")
        inputs = self.templater.template(task["ansible.controller.credential"]["inputs"])
        for name, value in inputs.items():
            with self.subTest(name=name):
                self.assertIsInstance(value, str)
        self.assertEqual(inputs["account_id"], "123456789012")
        self.assertEqual(inputs["root_volume_size"], "30")

    def test_release_snapshot_uses_the_same_json_boundary(self):
        block = next(t for t in self.tasks if "block" in t)["block"]
        task = next(t for t in block if "ansible.controller.credential" in t)
        material = self.templater.template(task["ansible.controller.credential"]["inputs"]["material"])
        self.assertIsInstance(material, str)
        self.assertEqual(json.loads(material), self.material)


if __name__ == "__main__":
    unittest.main()
