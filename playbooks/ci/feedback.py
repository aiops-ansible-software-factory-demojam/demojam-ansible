#!/usr/bin/env python3
"""Bound CI diagnostics and remove credentials before sharing with the author."""
import json
import os
import re
import sys


def idempotence_summary(text):
    """Keep the second convergence distinct from provisioning and cleanup."""
    failure = re.search(r'CRITICAL Idempotence test failed', text)
    if not failure:
        return ''
    phases = list(re.finditer(r'(?m)^INFO[^\n]*idempotence: Executing[^\n]*', text[:failure.start()]))
    if not phases:
        return ''
    phase = text[phases[-1].end():failure.start()]
    phase = re.sub(r'(?m)^\s*│\s?', '', phase)
    tasks = []
    for match in re.finditer(r'TASK \[([^\]]+)\]\s*\*+(.*?)(?=TASK \[|PLAY RECAP|\Z)', phase, re.S):
        hosts = re.findall(r'changed: \[([^\]]+)\]', match[2])
        if hosts:
            name = re.sub(r'\s*\n\s*│?\s*', ' ', match[1]).strip()
            tasks.append(name + ' (' + ', '.join(dict.fromkeys(hosts)) + ')')
    summary = ('Idempotence failed during the second convergence. A successful initial deployment '
               'does not establish idempotence. Cleanup recap counts are not the failure.\n')
    if tasks:
        summary += 'Tasks reporting changes in that phase:\n' + '\n'.join(tasks)
    return summary[:1200] + '\n'


def sanitize(report, environment):
    text = '\n'.join(str(report.get(key, '')) for key in ['stage', 'msg', 'stderr', 'stdout'])
    # Runner hides event UUID metadata behind cursor-control sequences.
    # Remove those frames before ordinary ANSI stripping exposes their base64.
    text = re.sub(r'\x1b\[K(?:[A-Za-z0-9+/=]+\x1b\[\d+D)+\x1b\[K', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    for key, value in environment.items():
        if re.search(r'KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL', key, re.I) and len(value) >= 8:
            text = text.replace(value, '[redacted]')
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer|token|basic)\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[redacted]', text)
    text = re.sub(r'(?i)((?:password|api[_-]?key|secret[_-]?(?:access[_-]?)?key|token)[\s\"\x27]*[:=][\s\"\x27]*)[^\s\"\x27,}]+', r'\1[redacted]', text)
    text = re.sub(r'(https?://)[^/\s:@]+:[^@\s/]+@', r'\1[redacted]@', text)
    summary = idempotence_summary(text)
    if len(summary + text) <= 6000:
        return summary + text
    # Molecule appends lengthy successful destruction logs after a failure.
    # Retain the failing stage and its recap instead of only the cleanup tail.
    failure = re.search(r'(?im)^.*(?:CRITICAL Idempotence test failed|fatal:|^ERROR:|^FAILED \(|Traceback \(most recent call last\)).*$', text)
    if failure:
        start = max(300, failure.start() - 2500)
        end = min(len(text), failure.end() + 1400)
        return (summary + text[:300] + '\n... failure context ...\n' + text[start:end]
                + '\n... cleanup tail ...\n' + text[-500:])[:6000]
    return text[:300] + '\n...\n' + text[-5690:]


if __name__ == '__main__':
    print(sanitize(json.load(sys.stdin), os.environ))
