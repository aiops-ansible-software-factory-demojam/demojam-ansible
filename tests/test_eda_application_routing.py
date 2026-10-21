"""Run production rule conditions against isolated events in the supported EDA DE.

Run this file with ansible-rulebook on PATH. Actions are replaced with debug;
the test never connects to AAP or launches an application job.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(os.environ.get('DEMO_ANSIBLE_SOURCE', Path(__file__).resolve().parents[1]))


class ApplicationEventRoutingTests(unittest.TestCase):
    def test_security_and_selinux_events_dispatch_only_to_their_own_repository(self):
        executable = shutil.which('ansible-rulebook')
        self.assertIsNotNone(executable, 'Run in the supported EDA decision environment')
        ruleset = yaml.safe_load((ROOT / 'rulebooks/forgejo-issue-remediation.yml').read_text())[0]
        baseline = {'payload': {'action': 'opened',
            'repository': {'full_name': 'demo-owner/ansible-collection-demo.pythonapp'},
            'issue': {'number': 2, 'state': 'open', 'pull_request': None,
                      'body': '<!-- remediation:fixture -->\nApplication: invoice-portal\nStructured security finding'}}}
        events = [baseline]
        nginx = copy.deepcopy(baseline)
        nginx['payload']['repository']['full_name'] = 'demo-owner/ansible-collection-demo.webapp'
        nginx['payload']['issue']['body'] = '<!-- demojam-webapp-outage -->\nRoot Cause: SELinux'
        events.append(nginx)
        for changes in ('wrong-repository', 'edited', 'closed', 'pull-request', 'missing-marker'):
            event = copy.deepcopy(baseline)
            if changes == 'wrong-repository':
                event['payload']['repository']['full_name'] = 'demo-owner/ansible-collection-demo.webapp'
            elif changes == 'edited':
                event['payload']['action'] = 'edited'
            elif changes == 'closed':
                event['payload']['issue']['state'] = 'closed'
            elif changes == 'pull-request':
                event['payload']['issue']['pull_request'] = {'id': 2}
            else:
                event['payload']['issue']['body'] = 'No remediation marker'
            events.append(event)
        wrong_nginx = copy.deepcopy(nginx)
        wrong_nginx['payload']['repository']['full_name'] = baseline['payload']['repository']['full_name']
        events.append(wrong_nginx)
        for index, rule in enumerate(ruleset['rules']):
            rule['action'] = {'debug': {'msg': f'APPLICATION_ROUTE_MATCH_{index}'}}
        ruleset['sources'] = [{'ansible.eda.generic': {'payload': events, 'event_delay': 0.1,
            'shutdown_after': 1, 'final_payload': {'routing_test_shutdown': True}}}]
        ruleset['rules'].append({'name': 'Finish isolated routing test',
            'condition': 'event.routing_test_shutdown == true', 'action': {'shutdown': {'delay': 0.1}}})
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'routing.yml'
            source.write_text(yaml.safe_dump([ruleset]))
            result = subprocess.run([executable, '--rulebook', str(source)],
                text=True, capture_output=True, timeout=90)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(output.count('APPLICATION_ROUTE_MATCH_0'), 1, output)
        self.assertEqual(output.count('APPLICATION_ROUTE_MATCH_1'), 1, output)


if __name__ == '__main__':
    unittest.main()
