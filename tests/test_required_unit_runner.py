"""A real passing, failing, empty or skipped suite must receive the correct CI verdict."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / 'playbooks/ci/run-unit-tests.py'


class RequiredUnitRunnerTests(unittest.TestCase):
    def test_only_executed_successful_coverage_passes(self):
        for case, decorator, assertion in [('pass', '', 'self.assertTrue(True)'),
                                           ('fail', '', 'self.fail("reproduction")'),
                                           ('skip', '@unittest.skip("unwritable fixture")', 'self.fail()'),
                                           ('xfail', '@unittest.expectedFailure', 'self.fail()'),
                                           ('empty', '', '')]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'tests').mkdir()
                if case != 'empty':
                    (root / 'tests/test_example.py').write_text('import unittest\nclass Example(unittest.TestCase):\n'
                                                               f'    {decorator}\n    def test_example(self):\n        {assertion}\n')
                run = subprocess.run([sys.executable, str(RUNNER), 'tests'], cwd=root,
                                     text=True, capture_output=True, timeout=20)
                self.assertEqual(run.returncode, 0 if case == 'pass' else 1, run.stdout + run.stderr)
                self.assertIn('CI_UNIT_RESULT', run.stdout)
                if case == 'skip':
                    self.assertIn('do not skip', run.stderr)
