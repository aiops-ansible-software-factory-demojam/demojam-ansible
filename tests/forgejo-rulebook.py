"""Exercise the real incident condition in the pinned EDA decision environment.

Run with Python in de-supported-rhel9, mounting this repository at /work.
Only the event source and action are replaced; the production rule is evaluated
by ansible-rulebook. No cluster, controller, or credentials are needed.
"""

from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile

import yaml


root = Path(__file__).resolve().parents[1]
rulebook = yaml.safe_load((root / "rulebooks/forgejo-issue-remediation.yml").read_text())
incident = {
    "payload": {
        "action": "opened",
        "repository": {"full_name": "demo-owner/ansible-collection-demo.webapp"},
        "issue": {
            "number": 7,
            "state": "open",
            "pull_request": None,
            "body": "<!-- demojam-webapp-outage -->\nRoot Cause: nginx SELinux denial",
        },
    },
    "test_case": "accepted_incident",
}
events = [incident]
for name, change in [
    ("edited", lambda p: p.update(action="edited")),
    ("reopened", lambda p: p.update(action="reopened")),
    ("closed", lambda p: p["issue"].update(state="closed")),
    ("other_repo", lambda p: p["repository"].update(full_name="other/repo")),
    ("starter", lambda p: p["issue"].update(body="Add a README test")),
    ("no_rca", lambda p: p["issue"].update(body="<!-- demojam-webapp-outage -->")),
    ("pull_request", lambda p: p["issue"].update(pull_request={"url": "test"})),
    ("no_issue", lambda p: p.pop("issue")),
]:
    event = deepcopy(incident)
    event["test_case"] = "rejected_" + name
    change(event["payload"])
    events.append(event)
rulebook[0]["sources"] = [{"ansible.eda.generic": {"payload": events}}]
rulebook[0]["rules"][0]["action"] = {
    "debug": {"msg": "RULE_MATCH={{ event.test_case }}"}
}
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "rulebook.yml"
    path.write_text(yaml.safe_dump(rulebook))
    result = subprocess.run(
        ["ansible-rulebook", "--rulebook", str(path)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    output = result.stdout + result.stderr
    if result.returncode or "RULE_MATCH=accepted_incident" not in output:
        raise SystemExit(output)
    if "RULE_MATCH=rejected_" in output:
        raise SystemExit(output)
    print("PASS incident accepted; edits, reopens, closed issues, wrong repos, "
          "starter issues, missing RCA, PRs, and missing issues ignored")
