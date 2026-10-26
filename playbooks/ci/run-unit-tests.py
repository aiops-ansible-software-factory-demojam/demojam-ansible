#!/usr/bin/env python3
"""Run required collection unit tests; skipped coverage is not a passing check."""
import json
import os
import sys
import unittest


def main():
    sys.path.insert(0, os.getcwd())
    suite = unittest.defaultTestLoader.discover(sys.argv[1] if len(sys.argv) > 1 else 'tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = (result.wasSuccessful() and result.testsRun > 0
              and not result.skipped and not result.expectedFailures)
    print('CI_UNIT_RESULT ' + json.dumps({'passed': passed, 'total': result.testsRun,
          'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures)}), flush=True)
    if result.skipped or result.expectedFailures:
        print('Required unit coverage did not execute successfully. Fix the tests or their fixtures; '
              'do not skip, disable or weaken required coverage.', file=sys.stderr)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
