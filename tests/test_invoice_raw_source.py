"""Run the real deployment preflight as AAP does, without installing its collection."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('DEMO_PYTHONAPP_SOURCE', ROOT.parent / 'ansible-collection-demo.pythonapp'))


class RawSourceTests(unittest.TestCase):
    def test_release_preflight_resolves_from_raw_controller_checkout(self):
        deployment = yaml.safe_load((SOURCE / 'playbooks/deploy-invoice.yml').read_text())[0]
        material = {'profile': {'schema_version': 1, 'application': {'id': 'app', 'release_manifest': 'releases/app.json'},
            'environment': 'test', 'package': {'name': 'PyPDF2'}, 'runtime': {'wheel_tags': ['py3-none-any']},
            'advisories': [{'id': 'CVE-TEST', 'source': 'fixture', 'affected_versions': ['3.0.1'], 'fixed_versions': ['3.0.1+rhlw.1']}],
            'validation': {'scenario': 'invoice_portal', 'business_checks': [{'path': '/healthz'}]}},
            'lightwell': {'channel': 'lightwell', 'package': 'PyPDF2', 'version': '3.0.1+rhlw.1',
                'filename': 'pypdf2-3.0.1+rhlw.1-1-py3-none-any.whl', 'sha256': 'b'*64,
                'url': 'https://packages.redhat.com/wheel.whl', 'provenance_url': 'https://packages.redhat.com/provenance', 'cves': ['CVE-TEST']}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'playbooks').mkdir(); (root/'releases').mkdir(); (root/'empty-collections').mkdir()
            (root/'releases/app.json').write_text(json.dumps({'schema_version':1,'application':'app','channel':'lightwell',
                'dependencies':[{'package':'PyPDF2','version':'3.0.1+rhlw.1','sha256':'b'*64}]}))
            for installed_name in [False, True]:
                tasks = copy.deepcopy(deployment['tasks'][:2])
                tasks[0]['ansible.builtin.import_tasks'] = str(SOURCE/'playbooks/tasks/validate-source.yml')
                if installed_name:
                    tasks[1]['ansible.builtin.include_role']['name'] = 'demo.pythonapp.lightwell'
                play = root/'playbooks/check.yml'
                play.write_text(yaml.safe_dump([{'name':'Execute actual preflight from an uninstalled checkout','hosts':'localhost',
                    'connection':'local','gather_facts':False,'tasks':tasks,'vars':{'invoice_material':material,
                    'invoice_candidate_sha':'a'*40,'invoice_release_channel':'lightwell','awx_job_id':101,'awx_job_scm_branch':'a'*40}}]))
                result = subprocess.run(['ansible-playbook','-i','localhost,',str(play)],text=True,capture_output=True,timeout=60,
                    env=dict(os.environ,ANSIBLE_COLLECTIONS_PATH=str(root/'empty-collections'),ANSIBLE_ROLES_PATH=str(SOURCE/'roles')))
                with self.subTest(installed_name=installed_name):
                    self.assertEqual(result.returncode==0, not installed_name, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
