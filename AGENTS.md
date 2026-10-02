# Agent guide

- AAP objects are inventory data under `group_vars/aap/`; apply them through
  `playbooks/aap/configure-aap.yml` and `infra.aap_configuration.dispatch`.
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
  certified dependencies come from the supported image. There is no CI.
- Write block-style YAML with FQCNs and named plays/tasks. Inventory carries
  runtime lookups; content consumes the values.
- `webapp_vm` manages only webapp in webapp-vms. The generic VM playbook stays
  confined to automation-vms. Preserve the `host: demo_cluster` launch variable.
- Verify cluster identity and current user authorization before live changes.
- Read README.md for bootstrap, public SCM, and reset behavior.
