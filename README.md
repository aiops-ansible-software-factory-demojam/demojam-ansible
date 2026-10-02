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
cannot see the supported image's collection path during installation, so four
`type: dir` entries copy CaC's certified dependencies from that image into the
cache. They require no downloads or Hub authentication. The supported image
already contains the Virtualization collection and required Python/system packages.

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
`DEMO_VM_SSH_PUBLIC`, `DEMO_RHEL_ENTITLEMENT_FILE`, `DEMO_EDA_WEBHOOK_TOKEN`,
and `DEMO_FORGEJO_TOKEN`. The entitlement is injected as a private file to avoid process environment size
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

In AAP, launch **webapp_vm**, then **webapp_nginx**. The first clones the
cluster's `rhel9` DataSource into `webapp-vms` and adds the generated public
SSH key through cloud-init. The second refreshes discovery, waits for SSH,
enables the entitled RHEL 9 BaseOS/AppStream repositories, and uses
`demo.webapp.nginx` from the public example collection. Controller installs
that collection from `requirements.yml` on project synchronization;
No Galaxy upload is required.

VM inventory queries only `automation-vms` and `webapp-vms`. The webapp is
`webapp-webapp-vms` in group `webapps`; its SSH hostname is the internal Service.
GitOps owns the HTTP/SSH Services, HTTPS Route and blackbox probe. The probe
becomes healthy once nginx serves HTTP. No automatic CI or schedule is added.

From openshift-gitops, the corresponding commands are:

```bash
make webapp-create
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

The **openshift_virtualization_machine** template remains available for
CirrOS/Fedora examples in `automation-vms`, with `host: demo_cluster` and
`vm_state: present` or `absent`. It is separate from the fixed RHEL webapp.

## Reset

`make demo-reset` in openshift-gitops wipes Forgejo and developer-agent
sessions, then seeds this starting config and the README feature issue.
It preserves AAP runtime credentials and existing VMs. Delete the webapp
first when testing fresh provisioning, then reset, bootstrap, and launch it
again. Normal hydration preserves user commits; config changes must be pushed
to Forgejo before dispatch. Manifest ZIPs and `.env` files are excluded from
fixture snapshots.

## Webapp outage issues

`group_vars/aap/eda.yml` configures the authenticated Alertmanager event stream
and activation. The supported `ansible.eda` modules apply the static stream
UUID and activation source mapping after dispatch. Project updates restart the
activation so updated rulebooks take effect.

`rulebooks/webapp-alert-issue.yml` accepts firing `WebappDown` notifications
from `blackbox-exporter` and starts `webapp_alert_issue`. The destination is
fixed in `group_vars/aap/webapp_issue.yml`: Forgejo collection repository
`demo-owner/ansible-collection-demo.webapp`. Its credential injects `FORGEJO_API_TOKEN`
only into the issue job. Existing open outage issues are reused; template
execution is serialized. Resolved notifications do not close issues.
