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
`playbooks/aap/configure-invoice.yml` and inventory under
`group_vars/invoice_factory`. It adds application observation, deduplicated
security issues, independent validation using a scoped EC2 test identity, and
production workflows bound to a successfully tested collection SHA.
The Forgejo EDA rule dispatches only newly opened invoice incidents to the
invoice AO authoring workflow. See the GitOps repository's detailed
[invoice walkthrough](https://github.com/aiops-ansible-software-factory-demojam/openshift-gitops/blob/feature/lightwell-invoice-demo/docs/invoice-lightwell-demo.md).
