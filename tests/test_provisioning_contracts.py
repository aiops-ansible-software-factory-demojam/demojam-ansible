"""Check rendered infrastructure contracts without provisioning live resources."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml

ROOT = Path(__file__).resolve().parents[1]
DNS_LABEL = re.compile(r'^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$')


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def tasks_in(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from tasks_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from tasks_in(child)


class ProvisioningContractTests(unittest.TestCase):
    def test_aws_endpoints_preserve_service_port_names(self):
        play = load('playbooks/aws/application-launch.yml')
        publisher = next(task for task in tasks_in(play)
                         if 'kubernetes.core.k8s' in task and 'loop' in task
                         and 'definition' in task['kubernetes.core.k8s'])
        engine = Environment(undefined=StrictUndefined)
        for application in ('webapp', 'pythonapp'):
            for item in publisher['loop']:
                item = dict(item, name=engine.from_string(item['name']).render(aws_application_name=application))
                definition = engine.from_string(publisher['kubernetes.core.k8s']['definition']).render(
                    item=item, aws_application_name=application,
                    application_aws_guest={'instances': [{'public_ip_address': '192.0.2.10'}]})
                endpoint = yaml.safe_load(definition)
                with self.subTest(application=application, port=item['port_name']):
                    self.assertRegex(endpoint['ports'][0]['name'], DNS_LABEL)
                    self.assertEqual(endpoint['ports'][0]['name'], item['port_name'])
                    self.assertEqual(endpoint['metadata']['labels']['kubernetes.io/service-name'], item['name'])

    def test_virtualization_device_names_are_valid_and_match_references(self):
        for filename in ('virtualmachine-manage.yml', 'webapp-launch.yml'):
            play = load('playbooks/openshift_virtualization/' + filename)
            task = next(task for task in tasks_in(play)
                        if 'spec' in task.get('redhat.openshift_virtualization.kubevirt_vm', {}))
            spec = task['redhat.openshift_virtualization.kubevirt_vm']['spec']
            for source in ('containerdisk', 'datasource'):
                with self.subTest(playbook=filename, source=source):
                    volumes = spec['volumes']
                    if isinstance(volumes, str):
                        volumes = NativeEnvironment(undefined=StrictUndefined).from_string(volumes).render(
                            vm_volumes=task['vars']['vm_volumes'], vm_disk_source=source)
                    devices = spec['domain']['devices']
                    for entries in (volumes, devices['disks'], devices['interfaces'], spec['networks']):
                        names = [entry['name'] for entry in entries]
                        self.assertEqual(len(names), len(set(names)))
                        for name in names:
                            self.assertRegex(name, DNS_LABEL)
                    self.assertEqual({disk['name'] for disk in devices['disks']},
                                     {volume['name'] for volume in volumes})
                    self.assertEqual({interface['name'] for interface in devices['interfaces']},
                                     {network['name'] for network in spec['networks']})

    def test_mcp_installs_the_certificate_paths_consumed_by_the_service(self):
        play = load('playbooks/webapp/mcp-install.yml')
        task = next(task for task in tasks_in(play)
                    if task.get('ansible.builtin.copy', {}).get('dest') == '/etc/webapp-mcp/{{ item.name }}')
        engine = Environment(undefined=StrictUndefined)
        installed = {engine.from_string(task['ansible.builtin.copy']['dest']).render(item=item)
                     for item in task['loop']}
        template = (ROOT / 'playbooks/webapp/templates/webapp-mcp.env.j2').read_text()
        required = set(re.findall(r'^(?:LINUX_MCP_TLS_CERT|LINUX_MCP_TLS_KEY|SSL_CERT_FILE)=(.+)$',
                                  template, re.MULTILINE))
        self.assertEqual(len(required), 3)
        self.assertTrue(required <= installed, f'Missing service material: {required - installed}')

    def test_mcp_notifications_run_their_handlers(self):
        play = load('playbooks/webapp/mcp-install.yml')[0]
        topics = set()
        for task in tasks_in(play['tasks']):
            notify = task.get('notify', [])
            topics.update([notify] if isinstance(notify, str) else notify)
        self.assertTrue(topics)
        # Keep the actual handler binding; replace privileged actions only.
        handlers = [{key: value for key, value in handler.items() if key in ('name', 'listen')}
                    for handler in play['handlers']]
        for index, handler in enumerate(handlers):
            handler['ansible.builtin.debug'] = {'msg': f'MCP_HANDLER_RAN_{index}'}
        fixture = [{'name': 'Exercise MCP notification bindings', 'hosts': 'localhost',
                    'connection': 'local', 'gather_facts': False, 'handlers': handlers,
                    'tasks': [{'name': 'Simulate changed MCP configuration',
                               'ansible.builtin.debug': {'msg': 'Configuration changed'},
                               'changed_when': True, 'notify': sorted(topics)}]}]
        with tempfile.TemporaryDirectory(prefix='mcp-handler-') as directory:
            path = Path(directory) / 'check.yml'
            path.write_text(yaml.safe_dump(fixture))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(path)],
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for index in range(len(handlers)):
            self.assertIn(f'MCP_HANDLER_RAN_{index}', result.stdout)

    def test_project_sync_copies_supported_image_dependencies(self):
        dependencies = load('requirements.yml')['collections']
        for collection in ('ansible/controller', 'ansible/platform', 'ansible/hub', 'ansible/eda', 'amazon/aws'):
            with self.subTest(collection=collection):
                source = '/usr/share/ansible/collections/ansible_collections/' + collection
                requirement = next((entry for entry in dependencies if entry['name'] == source), None)
                self.assertIsNotNone(requirement, f'Missing supported-image dependency: {collection}')
                self.assertEqual(requirement.get('type'), 'dir')
                self.assertNotIn('source', requirement)


if __name__ == '__main__':
    unittest.main()
