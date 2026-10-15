"""Verify useful, bounded audit evidence without exposing the raw audit file."""

import importlib.util
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'playbooks/webapp/files/webapp_mcp_audit.py'
spec = importlib.util.spec_from_file_location('audit_projection', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditFacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'audit.log'
        self.enforce = self.root / 'enforce'
        self.enforce.write_text('1\n')
        self.now = int(time.time())

    def line(self, *, stamp=None, comm='nginx', name='index.html'):
        return (f'type=AVC msg=audit({self.now if stamp is None else stamp}.123:42): '
                f'avc: denied {{ read open }} for comm="{comm}" name="{name}" '
                'scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:default_t:s0 '
                'tclass=file permissive=0 password=PRIVATE_PAYLOAD\n')

    def collect(self, text):
        self.log.write_text(text)
        return audit.collect(self.log, self.enforce, self.now)

    def test_only_recent_nginx_denials_are_exposed(self):
        facts = self.collect(self.line() + self.line(comm='sshd') + self.line(stamp=self.now-2000) + 'PRIVATE unrelated record\n')
        self.assertEqual(facts['status'], 'observed')
        self.assertEqual(facts['selinux_mode'], 'Enforcing')
        self.assertEqual(facts['matching_events'], 1)
        self.assertEqual(facts['events'][0]['target_context'], 'unconfined_u:object_r:default_t:s0')
        self.assertEqual(facts['events'][0]['operations'], 'read open')
        self.assertNotIn('PRIVATE', audit.encode(facts))

    def test_large_unicode_records_and_tail_are_bounded(self):
        facts = self.collect('unrelated ' * 10000 + '\n' + self.line(name='界'*400)*20)
        self.assertLessEqual(len(audit.encode(facts)), 3000)
        self.assertTrue(facts['truncated'])
        self.assertLessEqual(len(facts['events']), 4)
        self.assertGreater(len(facts['events']), 0)
        self.assertNotIn('PRIVATE', audit.encode(facts))

    def test_missing_and_symlink_sources_are_unavailable(self):
        self.assertEqual(audit.collect(self.log, self.enforce, self.now)['status'], 'unavailable')
        other = self.root / 'other'
        other.write_text(self.line())
        self.log.symlink_to(other)
        self.assertEqual(audit.collect(self.log, self.enforce, self.now)['status'], 'unavailable')

    def test_empty_evidence_is_explicit_and_mode_is_current(self):
        self.enforce.write_text('0')
        facts = self.collect('type=USER_LOGIN PRIVATE\n')
        self.assertEqual(facts['selinux_mode'], 'Permissive')
        self.assertEqual(facts['events'], [])
        self.assertIn('absence does not rule out SELinux', facts['limitations'][0])

    def test_rotation_reads_the_new_current_file(self):
        self.collect(self.line(name='old-file'))
        self.log.rename(self.root / 'audit.log.1')
        facts = self.collect(self.line(name='new-file'))
        self.assertEqual([event['name'] for event in facts['events']], ['new-file'])

    def test_snapshot_freshness_and_errors(self):
        snapshot = self.root / 'facts.json'
        facts = self.collect(self.line())
        snapshot.write_text(audit.encode(facts))
        self.assertEqual(audit.read_snapshot(snapshot, self.now)['events'], facts['events'])
        self.assertEqual(audit.read_snapshot(snapshot, self.now+31)['status'], 'unavailable')
        self.assertEqual(audit.read_snapshot(snapshot, self.now-10)['status'], 'unavailable')
        for value in ['not JSON', 'x'*3001, '[]', '{}', audit.encode(audit.unavailable('failure', self.now))]:
            snapshot.write_text(value)
            self.assertEqual(audit.read_snapshot(snapshot, self.now)['status'], 'unavailable')

    def test_publication_is_atomic_and_not_writable_by_daemon(self):
        snapshot = self.root / 'facts.json'
        with patch.object(audit, 'SNAPSHOT', snapshot), patch.object(audit, 'collect', return_value=self.collect(self.line())):
            audit.publish()
            audit.publish()
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o640)
        self.assertEqual(json.loads(snapshot.read_text())['status'], 'observed')
        self.assertEqual(list(self.root.glob('tmp*')), [])


if __name__ == '__main__':
    unittest.main()
