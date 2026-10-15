#!/usr/bin/env python3
"""Project recent nginx AVC denials for the unprivileged MCP service.

The root-owned systemd collector has fixed inputs and no network listener. MCP
reads only its atomic, bounded snapshot; it cannot request arbitrary audit data.
"""

import json
import os
import re
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG = Path('/var/log/audit/audit.log')
ENFORCE = Path('/sys/fs/selinux/enforce')
SNAPSHOT = Path('/run/webapp-mcp-audit/facts.json')
MAX_CHARS = 3000
MAX_BYTES = 65536
MAX_AGE = 30
WINDOW = 900
FIELDS = (
    ('audit_event', r'\bmsg=audit\(([^)]+)\)', 40),
    ('operations', r'avc:\s+denied\s+\{\s*([^}]+)', 48),
    ('comm', r'\bcomm="([^"]*)"', 32),
    ('name', r'\bname="([^"]*)"', 64),
    ('source_context', r'\bscontext=([^\s]+)', 96),
    ('target_context', r'\btcontext=([^\s]+)', 96),
    ('target_class', r'\btclass=([^\s]+)', 32),
    ('permissive', r'\bpermissive=([01])', 1),
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def unavailable(reason, now=None):
    return {'status': 'unavailable', 'collected_at': datetime.fromtimestamp(
        time.time() if now is None else now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'events': [], 'limitations': [reason]}


def collect(log_path=AUDIT_LOG, enforce_path=ENFORCE, now=None):
    """Read a bounded tail, returning only recent nginx AVC fields, never raw logs."""
    now = time.time() if now is None else now
    try:
        descriptor = os.open(log_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                return unavailable('Audit source is not a regular file.', now)
            offset = max(0, info.st_size - MAX_BYTES)
            source.seek(offset)
            data = source.read(MAX_BYTES)
        lines = data.decode('utf-8', errors='replace').splitlines()
        if offset and lines:
            lines = lines[1:]  # The bounded tail can begin inside a record.
    except OSError:
        return unavailable('Audit collection failed; denial evidence is unavailable.', now)
    try:
        mode = {'1': 'Enforcing', '0': 'Permissive'}.get(enforce_path.read_text().strip(), 'unknown')
    except FileNotFoundError:
        mode = 'Disabled'
    except OSError:
        mode = 'unknown'
    facts = {'status': 'observed', 'collected_at': datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'selinux_mode': mode, 'window_seconds': WINDOW, 'source': 'audit.log',
             'matching_events': 0, 'truncated': bool(offset), 'events': [],
             'limitations': ['Only recent nginx AVC denials from the current audit file are included; absence does not rule out SELinux.']}
    events = []
    for line in lines:
        if not re.search(r'\bavc:\s+denied\s+\{', line) or not re.search(r'\bcomm="nginx"(?:\s|$)', line):
            continue
        stamp = re.search(r'\bmsg=audit\((\d+(?:\.\d+)?):\d+\)', line)
        if stamp is None or not now - WINDOW <= float(stamp[1]) <= now + 5:
            continue
        event = {}
        for key, pattern, limit in FIELDS:
            match = re.search(pattern, line)
            value = match[1].strip() if match else ''
            facts['truncated'] |= len(value) > limit
            event[key] = value[:limit]
        events.append(event)
    facts['matching_events'] = len(events)
    facts['truncated'] |= len(events) > 4
    for event in events[-4:]:
        candidate = facts | {'events': facts['events'] + [event]}
        if len(encode(candidate)) <= MAX_CHARS:
            facts['events'].append(event)
        else:
            facts['truncated'] = True
    return facts


def publish():
    """Atomic publication under a root-owned, group-readable runtime directory."""
    descriptor, name = tempfile.mkstemp(dir=SNAPSHOT.parent)
    try:
        with os.fdopen(descriptor, 'w') as target:
            os.fchmod(target.fileno(), 0o640)
            target.write(encode(collect()))
        os.replace(name, SNAPSHOT)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_snapshot(path=SNAPSHOT, now=None):
    """Reject missing, malformed, oversized or stale evidence without leaking it."""
    now = time.time() if now is None else now
    try:
        with path.open() as source:
            data = source.read(MAX_CHARS + 1)
        if len(data) > MAX_CHARS:
            raise ValueError('Oversized snapshot')
        facts = json.loads(data)
        timestamp = datetime.strptime(facts['collected_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
        if not 0 <= now - timestamp <= MAX_AGE or facts.get('status') != 'observed':
            raise ValueError('Unavailable or stale snapshot')
        if not isinstance(facts.get('events'), list) or len(facts['events']) > 4:
            raise ValueError('Invalid event set')
        return facts
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable('Fresh audit evidence is unavailable; use the AAP audit fallback.', now)


if __name__ == '__main__':
    publish()
