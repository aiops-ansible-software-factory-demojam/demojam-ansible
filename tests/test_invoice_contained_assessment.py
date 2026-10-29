"""Check actual finding construction keeps availability separate from CVE state."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]

class ContainedAssessmentTests(unittest.TestCase):
    def check(self, running):
        names = ['Select availability separately from vulnerability state',
                 'Record each host finding without inferring a CVE from a timeout']
        tasks = [t for t in yaml.safe_load((ROOT / 'playbooks/invoice/check.yml').read_text())[0]['tasks'] if t['name'].split(' | ', 1)[-1] in names]
        tasks.append({'name': 'Require independent vulnerability and availability evidence', 'ansible.builtin.assert': {'that': [
            'remediation_finding.assessment_state == "affected"',
            'remediation_finding.business_passed == expected_business',
            'remediation_finding.installed_version == "1.0"']}})
        values = {'remediation_profile': {'application': {'service': 'invoice-portal'}},
            'ansible_facts': {'services': {'invoice-portal.service': {'state': 'running' if running else 'stopped'}}},
            'lightwell_finding': {'assessment_state': 'affected', 'installed_version': '1.0'},
            'invoice_status': {'json': {'release': {}}} if running else {'skipped': True},
            'remediation_business': {'results': [{'json': {'status': 'completed'}}] if running else [{'skipped': True}]},
            'invoice_regression': {'skipped': True}, 'expected_business': running}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'check.yml'
            path.write_text(yaml.safe_dump([{'name': 'Assess a contained application', 'hosts': 'localhost',
                'connection': 'local', 'gather_facts': False, 'tasks': tasks}]))
            r = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path), '-e', json.dumps(values)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stopped_service_is_still_affected_but_not_business_healthy(self): self.check(False)
    def test_running_service_preserves_normal_observations(self): self.check(True)
