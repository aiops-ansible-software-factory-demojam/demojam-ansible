# Agent guide

- Keep static hosts and backend plugin sources together under `inventory/`, beside
  `inventory/group_vars/`. The default source is `inventory/hosts.yml`; AAP selects
  an explicit backend plugin file so unrelated sources are not queried.

- Shared AAP objects are inventory data under `inventory/group_vars/aap/`; apply them through
  `playbooks/aap/configure-aap.yml` and `infra.aap_configuration.dispatch`.
  Invoice platform and release resources are scoped vars files under `playbooks/invoice/vars/`,
  applied by `playbooks/aap/configure-invoice.yml` through the same dispatcher.
  Application details belong in the collection's `roles/python_application/vars/invoice.yml`, not inventory
  or an application wrapper role.
- Bootstrap exclusively owns the demo organization, demo-aap-ee, public
  demojam-ansible project, base demo-inventory and aap group/host,
  demo-galaxy, demo-aap-dispatch and its type, and aap_configure_all. Do not
  redefine these objects in config-as-code.
- Config-as-code owns VM/SSH/RHEL credential types and credentials, inventory
  sources, demo job templates, EDA configuration, and gateway authentication. Inventory resolves
  runtime material from the dispatch environment under secure logging. Never
  commit secret values or generate/rotate the source key material here.
- Use Red Hat ee-supported-rhel9 for playbook syntax checks. Run Ansible lint
  in the development tools environment. Project requirements install public
  Galaxy CaC and the public Git demo collection;
  certified dependencies come from the supported image. There is no repository-hosted
  CI runner. Python independent acceptance runs in AAP and publishes its protected
  status; nginx uses independent operator validation before merge/recovery.
- Write block-style YAML with FQCNs and named plays/tasks. Inventory carries
  runtime lookups; content consumes the values.
- `webapp_vm` manages only webapp in webapp-vms. The generic VM playbook stays
  confined to automation-vms. Preserve the `host: demo_cluster` launch variable.
- Verify cluster identity and current user authorization before live changes.
- Read README.md for bootstrap, public SCM, and reset behavior.

- `pythonapp_vm` manages only the separate AWS `pythonapp` host and its `pythonapp-vms` endpoints. Python jobs target `pythonapps` and use `demo.pythonapp`; SELinux/nginx jobs stay on `webapps`. Preserve this separation in inventory, tags and EDA repository routing.

- Guest-provisioning Molecule scenarios must destroy owned test resources before
  `create` in every test/converge/create/check sequence. Preserve cleanup ownership
  records and stop on cleanup failure. Controller-only logic fixtures require no
  test guest. Idempotence is a second converge within one fresh lifecycle.

## Ansible authoring standard

Follow [Red Hat CoP Automation Good Practices](https://redhat-cop.github.io/automation-good-practices/),
especially role design, coding style, idempotence and check mode. Repository and
operator requirements still determine application layout and acceptance boundaries.

Prefer an existing built-in or maintained collection module to `command` or
`shell`; check its interface with `ansible-doc` and declare production collection
dependencies in `galaxy.yml` when applicable. Use `ansible.builtin.uri` for HTTP
APIs. Avoid custom modules/plugins when existing modules and straightforward tasks
are sufficient; document the specific unsupported capability before adding one.
Keep application details in vars files, not role defaults or inventory.

If a command is necessary, comment why no suitable module covers it, prefer
`ansible.builtin.command` with `argv`, and provide accurate change detection,
check-mode behavior and failure handling. Use shell only when shell features are
required. Never suppress configuration/test errors with `|| true` or unconditional
`failed_when: false`, and never hide mutations with blanket `changed_when: false`.
Read-only diagnostic commands can legitimately report no change; classify expected
nonzero results explicitly and retain unexpected failures.

Keep meaningful regression assertions and the complete Molecule lifecycle,
including idempotence and verification. Run production-profile lint and all final
checks; preserve actual nonzero exit codes. Do not weaken tests or discard test
state to claim success. Report failed or unavailable checks honestly.
