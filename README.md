# demojam-ansible

This public Forgejo repository follows the homelab `group_vars/aap` and
`infra.aap_configuration.dispatch` model. Inventory, AAP object definitions,
and VM/webapp playbooks share one project. No SCM credential is required.

## Bootstrap boundary

Run `bash bootstrap/bootstrap.sh` from the **openshift-gitops** repository,
after populating its root `.env` and supplying `aap_manifest.zip`.

1. GitOps reconciles AAP, OpenShift Pipelines, VM namespaces/RBAC/Services,
   Forgejo and monitoring.
2. Bootstrap imports the license and reuses persistent Kubernetes API/SSH
   material. It owns only the foundation required to run config-as-code:
   organization `demo`, supported EE `demo-aap-ee`, public project
   `demojam-ansible`, base `demo-inventory` with the local `aap` host/group,
   Galaxy and dispatch credentials, the dispatch credential type, and
   `aap_configure_all`. It waits for the public project sync, then launches
   configuration inside AAP. No Resource Operator CRs or connection token
   are needed.
3. `group_vars/aap` and `infra.aap_configuration.dispatch` exclusively own
   the VM/SSH/RHEL credential types and credentials, SCM inventory sources,
   demo job templates, and EDA credentials/project/decision environment. Gateway OIDC configuration uses the supported
   `ansible.platform` modules. Runtime values arrive through the dispatch
   credential's environment variables and are consumed under secure logging.
   Static inventory supplies the VM API target; dynamic inventory discovers
   running VMs. Source credentials and VM SSH identity survive reruns.

There is no custom AAP image build or Automation Hub token. Root `requirements.yml`
installs `infra.aap_configuration` from public Galaxy and `demo.webapp` from
public Forgejo Git on project updates. Controller's isolated collection cache
cannot see the supported image's collection path during installation, so
`type: dir` entries copy CaC's certified dependencies from that image into the
cache. They require no downloads or Hub authentication. The supported image
already contains the Virtualization and AWS collections and the required Python/system packages.

SCM and Git collection URLs use `http://forgejo.forgejo.svc.cluster.local:3000`.
They are reachable from AAP project updates and execution pods, without
Forgejo authentication. For local development outside the cluster, use the supported EE and replace
that hostname with your Forgejo Route or use a port forward. No Hub or Forgejo
token is required for project requirements.

Dispatch reconciles runtime credential objects using the persistent material
supplied by bootstrap. Rerun `make aap-configure` to refresh the foundation,
license, runtime material and configuration. Subsequent inventory configuration
changes can use `make aap-sync`; the foundation changes remain in openshift-gitops.
The dispatch credential provides `AAP_HOST`, `AAP_USERNAME`, `AAP_PASSWORD`,
`AAP_EE_IMAGE`, `DEMO_OIDC_ISSUER`, `DEMO_OIDC_CLIENT_SECRET`, `DEMO_VM_API_HOST`,
`DEMO_VM_API_TOKEN`, `DEMO_VM_API_CA`, `DEMO_VM_SSH_PRIVATE`,
`DEMO_VM_SSH_PUBLIC`, `DEMO_RHEL_ENTITLEMENT_FILE`, `DEMO_WEBAPP_MCP_MATERIAL`, `DEMO_EDA_WEBHOOK_TOKEN`,
`DEMO_FORGEJO_EDA_WEBHOOK_TOKEN`, and `DEMO_FORGEJO_TOKEN`. The entitlement is injected as a private file to avoid process environment size
limits. These inputs are required;
missing material fails before any AAP objects are changed.

Before dispatch, `configure-aap.yml` waits until EDA's organization API can
resolve `demo`. EDA synchronizes shared organizations on a 15-minute schedule,
so the first configuration run allows 20 minutes for synchronization. The check
only reads the API; bootstrap still owns organization creation. If readiness
times out, the job fails with a synchronization message before applying
config-as-code objects. Check EDA worker health and gateway synchronization,
then rerun `make aap-configure`.

`group_vars/aap/oidc.yml` defines the independent demo Keycloak authenticator
and access maps. Bootstrap adds `DEMO_OIDC_ISSUER` and
`DEMO_OIDC_CLIENT_SECRET` to the runtime dispatch credential; these values
are required when running `configure-aap.yml`. The supported EE's
`ansible.platform` modules configure the gateway after the CaC dispatcher.
Demo users join organization `demo`; `demo-admins` receive superuser access.
Bootstrap then reads the gateway's generated callback URL and registers that
exact URL in Keycloak. Regular organization membership does not grant every
job template's execution permission.

## Launch and automate the RHEL webapp

For initial demo setup in AAP, launch **webapp_vm**,
**webapp_selinux_permissive**, then **webapp_nginx**. Bootstrap runs these
jobs in that order. The VM job clones the
cluster's `rhel9` DataSource into `webapp-vms` and adds the generated public
SSH key through cloud-init. The separate setup playbook waits for SSH and
sets SELinux to Permissive so the unfixed collection can serve HTTP.
The nginx job refreshes discovery, waits for SSH,
enables the entitled RHEL 9 BaseOS/AppStream repositories, and uses
`demo.webapp.nginx` from the public example collection. Controller installs
that collection from `requirements.yml` on project synchronization;
No Galaxy upload is required.

The nginx installation playbook and base collection leave the SELinux mode
unchanged. After **webapp_selinux_enable** triggers the outage, merge the
tested collection fix and launch **webapp_nginx** once to deploy it with
SELinux still Enforcing. Do not run the permissive setup job during recovery.

CNV inventory queries only `automation-vms` and `webapp-vms`. The webapp is
`webapp-webapp-vms` in group `webapps`; its SSH hostname is the internal Service.
GitOps owns the HTTP/SSH Services, HTTPS Route and blackbox probe. The probe
becomes healthy once nginx serves HTTP. No automatic CI or schedule is added.

From openshift-gitops, the corresponding commands are:

```bash
make webapp-create
bash bootstrap/bootstrap.sh aap launch webapp_selinux_permissive
make webapp-nginx
make webapp-verify
make webapp-delete
```

`webapp-delete` launches the same VM template with `vm_state: absent`, removing
its owned disk. The namespace, Services and Route remain GitOps-managed.
The VM API account has no VM access outside the two demo namespaces. It can
clone OS disks but cannot create namespaces. TLS verification stays enabled.
The SSH key and API token are runtime demo credentials preserved by reruns.

## Generic VM example

In CNV mode, the **openshift_virtualization_machine** template remains available for
CirrOS/Fedora examples in `automation-vms`, with `host: demo_cluster` and
`vm_state: present` or `absent`. It is separate from the fixed RHEL webapp.

## Reset

`make demo-reset` in openshift-gitops wipes Forgejo and developer-agent
sessions, then seeds this starting config and the README feature issue.
It removes the monitored guest and disposable test guests while preserving AAP
and runtime credentials. Run bootstrap afterward to restore the permissive
unfixed baseline. AWS also preserves its network and selected images. Normal hydration preserves user commits; config changes must be pushed
to Forgejo before dispatch. Manifest ZIPs and `.env` files are excluded from
fixture snapshots.

## Webapp outage issues

`group_vars/aap/eda.yml` configures the authenticated Alertmanager event stream
and activation. The supported `ansible.eda` modules apply the static stream
UUID and activation source mapping after dispatch. Project updates restart the
activation so updated rulebooks take effect.

`rulebooks/webapp-alert-issue.yml` accepts firing `WebappDown` notifications
from `blackbox-exporter` and starts `call_ao_webhook` with the original alert
payload. Its AO service-account credential authenticates the call to the
`alertmanagealert` trigger. AO asks the configured model to investigate with MCP audit diagnostics first.
It runs `pull_audit_logs` only when MCP is disabled or the primary/audit evidence
is unavailable, then uses text-only RCA. One completed diagnosis reaches
`webapp_alert_issue` with the original alert.
The workflow and the policy allowing AAP to reach AO are in openshift-gitops.

The issue destination is fixed in `group_vars/aap/webapp_issue.yml`: Forgejo collection repository
`demo-owner/ansible-collection-demo.webapp`. Its credential injects `FORGEJO_API_TOKEN`
only into the issue job. Existing open outage issues are reused; template
execution is serialized. Resolved notifications do not close issues.

## Incident remediation

### Live diagnostics for AO RCA

The `pull_audit_logs` job publishes structured SELinux facts capped at 3,000
characters, including current enforcement mode, collection time, up to four
recent AVC events and explicit truncation/collection limitations. Only selected
event fields are included; raw audit stdout stays out of RCA prompts and AAP
task output. `affected_host_facts` is the new artifact; the existing
`affected_host_log_output` also carries that compact JSON during coordinated
workflow updates. `molecule test -s logic-audit-facts` exercises oversized Unicode
records, unavailable collection, benign entries and idempotence locally.

The CNV or AWS webapp runs the stock `linux-mcp-server==1.7.0` executable
under systemd as `mcpdiag`, using its built-in Streamable HTTP, TLS and JWT
support. AAP's `webapp_mcp` template applies `playbooks/webapp/mcp-install.yml`
after nginx and the RHEL repositories are configured. Python 3.12 dependencies
come from the hashed Linux x86_64 lock. There is no custom server, audit
collector, timer, snapshot or SELinux MCP tool.

Native `read_log_file` reads `/var/log/audit/audit.log` and the two nginx logs.
The account has journal access and read access to auditd's current log;
`log_group = mcpdiag` preserves access after auditd rotation. Files stay
root-owned and the daemon has no sudo, capabilities or audit-log write access.
The native policy allows nine diagnostic tools on localhost and excludes
script execution and general file reads. Service/journal tools retain their
native scope. The workflow requests at most 20 log lines; the upstream tool's
own limits apply, without our former 20-line/3000-character enforcement or
nginx-only argument middleware. Audit reads can include other host events.

Red Hat describes RHEL MCP as a Developer Preview and documents an AI-client-side
STDIO/SSH setup. This AO integration uses the package's native HTTPS transport
on the guest. See [RHEL MCP documentation alignment](docs/rhel-mcp.md) for that
difference, platform coverage and the rollout sequence.

Bootstrap issues a 90-day client-credentials JWT from the existing demo
Keycloak's dedicated `demojam-ao-rca` client. The stock verifier checks the
signature, issuer, audience and expiry against Keycloak's HTTPS JWKS endpoint.
The native tool policy also requires the current token ID (`jti`); rotation
replaces that ID and denies the old token access to tools. AO stores the bearer
credential separately for discovery and workflow execution and cannot renew
it itself. Bootstrap preserves material across reruns and renews tokens within
one day of expiry; `make webapp-mcp-rotate` forces renewal.

Inventory resolves `WEBAPP_MCP_MATERIAL_FILE`. The guest receives only public
JWT trust/claims and the TLS certificate/key/CA, with the system CA bundle plus
the cluster ingress CA for JWKS verification. The bearer token, Keycloak client
secret and CA signing key stay out of the guest. Guest connectivity to the
public Keycloak Route is required. No runtime token-verification code is added.

`webapp_mcp_state: absent` stops the daemon, closes 8443, removes TLS material
and restores root-only audit-log access, while preserving nginx and auditd.
Setup removes retired collector units, code and snapshot files on existing
installations. AAP remains the independent fallback if native MCP audit reads
are unavailable. AWS defaults to audit-only RCA; set `WEBAPP_MCP_ENABLED=true`
in openshift-gitops to enable its managed EC2 path.

Functional checks (install the locked requirements in `.venv` first):

```bash
molecule test -s playbook-webapp-mcp
.venv/bin/python tests/test_webapp_mcp.py -v
```

Run Python checks in an isolated environment with dependencies from
`playbooks/webapp/files/webapp-mcp-requirements.txt`. Set `WEBAPP_MCP_AO_IMAGE`
to the pinned AO backend image to exercise its shipped health/discovery
adapter and runtime tool provider. Molecule uses the pinned provisioner and a
checksum-pinned Rocky Linux 9 QEMU guest. `PROVISIONER=podman` selects the
alternative systemd container on hosts with working cgroup delegation. It tests the
production playbook, idempotence, authenticated diagnostics, denied access,
auditd/nginx rotation, reboot persistence and shutdown without breaking nginx. RHEL/CNV networking and
the full outage workflow require the installed demo as additional checks.

To regenerate the dependency lock on Linux x86_64 with Python 3.12, resolve
`linux-mcp-server==1.7.0 fastmcp==3.4.8` using
`pip install --dry-run --ignore-installed --report report.json`. Record every
selected package/version with its `download_info.archive_info.hashes.sha256`
in the lock, then install with `--require-hashes --only-binary=:all:` and rerun
the checks. Treat a new dependency resolution as a reviewed update.

Bootstrap also configures a separate authenticated Forgejo event stream and
the `forgejo-issue-remediation.yml` rulebook. The repository's issue webhook
reaches this listener using its own token. Only newly opened, open incidents
in the fixed collection repository with the outage marker and RCA match.
Edits, comments, starter issues, closed/reopened issues, and PRs are ignored.

The rulebook launches `call_ao_webhook` with `issue_number` and
`ao_webhook_path: forgejo-issue-remediation`. That job publishes
`ao_execution_id`. AO prepares the issue branch through Backstage and feeds
the complete issue and RCA into an Omnigent session. The coding agent preserves
SELinux enforcing, implements and tests the collection fix, and submits a PR;
a human merges after tests pass and launches one recovery job.

Both event streams have fixed UUIDs, persistent independent credentials, and
activation readiness checks. All bootstrap logic, hook registration, model
selection, and workflow publication live in openshift-gitops' existing
`bootstrap/bootstrap.sh`. Run that entry point to set up the whole demo.

## AWS guests

Bootstrap selects `DEMO_BACKEND=aws` in the dispatch credential. Config-as-code
keeps the same `webapp_vm`, setup, fault and recovery job names. `webapp_vm`
uses `playbooks/aws/webapp-launch.yml` to manage one deployment-tagged RHEL 9
EC2 instance and updates the EndpointSlices behind the existing webapp Services.
The AWS inventory discovers only running guests with that deployment ID and
purpose `webapp`; SSH uses the public address and `ec2-user`.

AWS jobs receive the bootstrap-created AAP key through `demo-aws-guest`.
The bootstrap root/admin key is never supplied to AAP. The dispatch credential
carries `DEMO_AWS_ACCESS_KEY_ID`, `DEMO_AWS_SECRET_ACCESS_KEY`, `DEMO_AWS_REGION`
and the saved nonsecret guest settings in `DEMO_AWS_CONFIG`. AWS nginx jobs use
the AMI's RHUI repositories and omit the workshop CDN entitlement setup.
The generic CNV job is registered only for CNV deployments.

Both backends provision first, run the separate permissive setup job, and then
install the unfixed collection. Recovery remains one `webapp_nginx` launch
after a test-gated merge, with SELinux still enforcing.
# Invoice factory configuration

The optional invoice scenario is configured by
`playbooks/aap/configure-invoice.yml` and vars files under
`vars/invoice`. AAP resource definitions are applied through the pinned
`infra.aap_configuration.dispatch` role. Application settings live in the
collection's `vars/invoice.yml`, consumed directly by `python_application`. It adds application observation, deduplicated
security issues, independent validation using a scoped EC2 test identity, and
production workflows bound to a successfully tested collection SHA.
The Forgejo EDA rule dispatches only newly opened invoice incidents to the
invoice AO authoring workflow. See the GitOps repository's detailed
[invoice walkthrough](https://github.com/aiops-ansible-software-factory-demojam/openshift-gitops/blob/feature/lightwell-invoice-demo/docs/invoice-lightwell-demo.md).

In the supported Red Hat execution environment, run
`python3 -m unittest discover -s tests -p test_invoice_configuration.py -v`
to check the invoice credential serialization with dummy inputs. These checks
exercise Ansible's native templating and do not connect to AWS or AAP.

The validator supplies a temporary localhost inventory only to `ansible-lint`'s
syntax checks. This preserves Controller's strict inventory parsing policy and
keeps lint separate from production and Molecule inventories. In the development
tools image, run `python3 tests/test_invoice_validation_inventory.py` to reproduce
the missing-inventory failure and check that the isolated inventory fixes it.

Before provisioning test guests, the validator runs the trusted acceptance source
contract against the exact candidate checkout. This exercises Ansible's variable
precedence and checks that rollback baseline preparation reads `files/invoice`
from that collection. The acceptance task supplies its source override at task
scope so the application's vars file cannot redirect it to the acceptance directory.
Run `python3 -m unittest discover -s tests -p test_invoice_acceptance_source.py -v`
locally with the Python collection checked out beside this repository, or set
`DEMO_PYTHONAPP_SOURCE` to its checkout. This check does not provision guests.

The invoice-named job entry points are compatibility names for the initial demo.
Findings and incident keys are profile-driven; package/advisory identities are
not hardcoded in the detection or ticket code. `invoice_discover` uses `demo.pythonapp.lightwell` task files with built-in
Ansible modules. It joins the pinned public Lightwell OSV advisory feed to the
PEP 691 JSON package index, requiring matching package/base version, complete
CVE coverage, runtime compatibility and artifact/provenance identity. Bootstrap
prepares the single eligible fix; ambiguity requires an operator approval pin.
Discovery in AAP must match that prepared version and hash. Unknown versions
remain unknown. Release validation uses Ansible assertions and a canonical JSON
digest; it no longer invokes a custom module or a Python policy script.

The mandatory suite under `playbooks/invoice/acceptance` is maintained separately
from the candidate collection. Validation runs a second, independently provisioned
RHEL 9/10 lifecycle, including business checks, actual installed wheel identity,
independent regression documents, SELinux, and an activation-failure canary test.
The candidate source remains clean. Failed cleanup preserves ownership metadata
for recovery; never mark a failed validation successful after rollback.
Promotion binds the tested source, wheel, execution image and application-profile
digest. Add reviewed acceptance adapters for applications beyond the invoice app.

## Independent application hosts

On AWS, `webapp_vm` and `pythonapp_vm` use the shared `playbooks/aws/application-launch.yml` lifecycle through fixed application wrappers. They discover distinct deployment/purpose tags and publish endpoints into `webapp-vms` and `pythonapp-vms`. Dynamic inventory sources populate separate `webapps` and `pythonapps` groups. SELinux fault/nginx jobs target only `webapps`; invoice observation/deployment target only `pythonapps` with the `demo.pythonapp` collection.

The Forgejo listener dispatches new Python security incidents from `demo-owner/ansible-collection-demo.pythonapp` using their structured remediation marker. The nginx RCA rule continues to target `demo-owner/ansible-collection-demo.webapp` and requires its outage marker and RCA. Application hosts share a bootstrap-managed SSH identity but have separate AAP credential names and EC2 key names; disposable test credentials remain separate from production provisioning.

An upgrade job, `webapp_retire_legacy_invoice`, stops/disables only a former invoice unit found on the nginx host after bootstrap has installed the independent Python baseline. It preserves release files and does not target the new Python host.

## Continuous invoice orchestration

The existing invoice EDA entry now feeds one AO execution through PR discovery,
independent validation, human merge, immutable launch contracts, human AAP
approval, deployment proof and application verification. `invoice_validation_status`
uses a dedicated credential carrying the configured Controller API identity and
Forgejo status writer, without production SSH or test AWS credentials. It reads
trusted validation evidence before publishing a required check.
`invoice_verify_deployment` reads the native workflow's approval/deployment nodes
and actual job source/artifact before the application check can close the incident.
The trusted project remains outside the coding agent's write permissions.

Release preparation requires `invoice_issue_number` and `invoice_pr_number` as
well as the exact `invoice_candidate_sha` and `invoice_validation_job_id`. The
feature gate checks that the matching incident branch was human-merged and that
its tested head is contained in main. Existing manual promotion supplies these
identities automatically. No playbook approves or merges on the operator's behalf.


Promotion resources are configured once: the existing `invoice-collection`
project, shared artifact credential, `invoice_deploy` JT and `invoice_promote`
workflow. No project, credential, JT or workflow is generated per release.
`invoice_prepare_release` checks human merge and independent validation, returning
a public release contract with source, validation/incident IDs, artifact and
material hashes, release-manifest hash and immutable execution image.

AO launches `invoice_promote` with that contract and the exact tested SCM commit.
The native AAP graph is `validate-release → production-approval → recheck-release
→ deploy`. Both release checks use trusted controller content without production
SSH. The post-approval check verifies this execution's actual human decision.
The collection rechecks source, lock, target group and material before guest
connection, closing the shared-credential drift window. The signing key remains
independently pinned. Post-deployment proof checks both guard jobs, approval,
actual source/image and emitted release evidence before AO closes the incident.

Older acceptance records without `material_sha256` cannot authorize this path;
run independent validation again. Old per-release objects are not deleted during
configuration, so historical or running executions remain available. Remove them
separately after confirming no execution still uses them.

CVE response routing reads actual successful `invoice_check` and `invoice_discover`
job receipts through `invoice_route`, validating template/project identity, complete
host evidence and current profile hashes. The four outcomes are `app_patch`,
`contain`, `log_only` and `manual_review`. A compatible approved Lightwell fix keeps
the existing authoring and promotion flow. Unknown evidence or unsupported ecosystems
require manual review; lower-severity no-fix findings retain a deferred review.
API/discovery errors stop rather than masquerading as a missing fix.

`invoice_record_response` maintains separate deduplicated security tickets with an
anchored review deadline. Their marker cannot trigger the coding agent. Temporary
containment uses a separate `invoice_contain` approval workflow: trusted receipt
check → human approval → trusted recheck → scoped collection service shutdown.
The request pins the configured application SCM revision, profile and exact hosts.
The collection rechecks live dependency identity, stops/disables only the configured
application service, and publishes verified containment with `fixed: false`.
A later approved fixed release restores service through the existing deployment
role. Only fixed versions with healthy business checks close either incident type.
No generic OS patching, CME integration or automatic review scheduler is included.

The base AAP dispatcher also installs `webapp-collection`, `webapp_review_release`,
`webapp_deploy`, `webapp_verify_deployment` and the reusable `webapp_promote`
workflow. These work in the web-only scenario without invoice configuration.
The source project allows an exact launch revision. Only the deploy JT has the
web SSH identity; trusted review/proof jobs use the separate deployment-review API
credential and never receive guest SSH access.

AO waits for a matching human-merged nginx incident PR, then launches
`validate-release → production-approval → recheck-release → deploy` with its exact
head SHA. Review gates verify merge/main ancestry through the web-specific feature
gate and bind native workflow/approval identities to the same incident and source.
Collection checks run before guest access; final proof requires every approved web
host to report enforcing SELinux, HTTP 200 and the exact deployed source. The old
`webapp_nginx` job remains baseline/setup automation. Independent operator RHEL
validation remains required before human merge. The coding agent cannot merge or
approve deployment.

### Independent CI and agent completion

AO keeps each authoring task open until the current PR head passes independent
CI. After publishing, the agent uses `demo-goldenpath ci <issue> --wait`; pending
exits 75, failures exit 1 with trusted diagnostics, and only exact-head success
exits 0. Failed checks return to the same session, branch and PR. New commits
invalidate old success. AO delivers failure feedback once per failed source revision,
whether the author is active or idle; it never sends an interrupt. Returning to idle
without a new commit does not trigger repeated messages or mark the work complete.
Human review/merge and native production
approval still happen after green CI.

`webapp_validate` runs lint, build, all unit tests, every Molecule scenario, and
trusted red/green/relabel recovery. `invoice_validate` runs all unit and Molecule
tests plus trusted invoice acceptance. Its preflight resolves the actual release
role from a raw AAP checkout without installing the candidate collection first.
The full Python unit suite runs as root on both disposable RHEL guests during
trusted preparation, using a separate Python environment and the hash-verified
baseline wheel. This preserves the real ownership checks and the role's `/opt`
restriction while the Controller execution remains unprivileged. Skipped tests
and expected failures cannot produce a passing receipt. Test subprocesses never inherit the reviewer or AAP API token.
`demo_ci_iteration` launches allowlisted validation/publisher jobs using the Controller API
and sends source-bound feedback only to the existing author, once per failed
commit across both active and idle states. It never interrupts or executes candidate code.
`demo_ci_publish` alone receives the reviewer credential, verifies job/project/SHA
provenance, and posts a required status and bounded, sanitized PR diagnostics.
A failed CI verdict permits another authoring attempt, never promotion. AO uses
a linear loop because this AO version retains skipped conditional branches
between loop iterations. Retry decisions use ordinary Ansible conditions in
the CI controller job. Version 2 receipts invalidate earlier incomplete checks.

Bootstrap configures these jobs with a pinned test image and disposable AWS or
CNV credentials. For an existing installation, publish the trusted controller
content first and run `bash bootstrap/bootstrap.sh ci-configure` from GitOps.
Published workflow changes apply to new executions; do not rewrite past results.

### Manual independent nginx regression diagnostics

Each fix must change `extensions/molecule/nginx/tasks/regression.yml` and import
those assertion tasks from the scenario's `verify.yml`. In the existing managed
sandbox, the operator runs this repository's trusted runner after the author is
idle, using the exact published commit and Forgejo's preserved `merge_base`:

```bash
source /etc/profile.d/molecule.sh
ansible-playbook -i localhost, /trusted/demojam-ansible/playbooks/webapp/validate-regression.yml \
  -e webapp_source_directory=/path/to/clean/candidate \
  -e webapp_candidate_sha=<published-head-sha> \
  -e webapp_base_sha=<incident-merge-base-sha>
```

The runner exports source without modifying the coding workspace. On every
disposable guest the same authored assertion must fail with the original code
and pass with the repaired code. Infrastructure failures and tests that already
pass the original are rejected. A trusted check independently invalidates file
labels, restores them from persistent SELinux policy, and requires enforcing HTTP
recovery. Both original and repaired lifecycles include idempotence and cleanup.
AWS requires both RHEL 9 and RHEL 10. CNV uses the configured CentOS Stream guest.
Receipts remain in the reported directory; failed cleanup remains a failed run.

The manual runner returns a regression-only receipt for diagnostics. It does
not satisfy the complete `webapp-ci-v2` gate. Normal promotion requires the
independent AAP validator's full lint/build/unit/Molecule/red/green verdict,
published by `demo_ci_publish`; do not substitute a manually created success.

Pinned deployment jobs use Controller's protected `awx_job_scm_branch` as the full
approved SHA. Only jobs without an override use `awx_project_revision`, which can
otherwise describe a later default-project merge commit.
