#!/usr/bin/env python3
"""Bound CI diagnostics and remove credentials before sharing with the author."""
import json
import os
import re
import sys


def sanitize(report, environment):
    text = '\n'.join(str(report.get(key, '')) for key in ['stage', 'msg', 'stderr', 'stdout'])
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    for key, value in environment.items():
        if re.search(r'KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL', key, re.I) and len(value) >= 8:
            text = text.replace(value, '[redacted]')
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|token|basic)\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[redacted]', text)
    text = re.sub(r'(?i)((?:password|api[_-]?key|secret[_-]?(?:access[_-]?)?key|token)[\s\"\x27]*[:=][\s\"\x27]*)[^\s\"\x27,}]+', r'\1[redacted]', text)
    text = re.sub(r'(https?://)[^/\s:@]+:[^@\s/]+@', r'\1[redacted]@', text)
    return text if len(text) <= 6000 else text[:300] + '\n...\n' + text[-5690:]


if __name__ == '__main__':
    print(sanitize(json.load(sys.stdin), os.environ))
