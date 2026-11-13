# network_config-backup SPEC

Status: review draft only. Do not implement, commit, or push until the owner says go.

Product owner: the repo owner. Implementer: an agent that has this file and no other conversation.

This file is the contract. The planning notes are not. If they disagree, this file wins. Do not invent a field, a command, a URL, or a platform string that this file does not list.

## 1. Invariants

1. The playbook repo contains no secret, no site name, no device name, no address, no personal domain, and no login name.
2. Device membership is not a list in git. A device is in a run only when NetBox says it is active, it has a primary IPv4, it has a platform, and that platform's `ansible_network_os` field is set.
3. `staged` devices are not contacted. Any status other than `active` is not contacted.
4. The playbook does not write built-in NetBox fields: name, status, role, platform, primary IP, serial.
5. The playbook writes nothing to NetBox. It reads the inventory and the platform metadata; it never PATCHes, POSTs, or deletes a NetBox object.
6. A failed contact or a redaction leak fails the host. No archive file is written for it and nothing is recorded in NetBox.
7. Git URLs do not carry credentials. The archive remote is `BACKUP_GIT_URL`. Unset means the files stay in the local working tree and nothing is pushed.
8. One archive repo per runner. Site and platform limits change which hosts run. They do not change the remote.
9. Config files live at `<site-slug>/<device-name>.cfg` at the archive repo root. There is no `archive/` directory.
10. The nightly job does not call `bootstrap_netbox.yml`.
11. `bootstrap_netbox.yml` creates and updates the two platform custom fields. It does not delete fields.

A build that breaks one of these is not done.

## 2. Repositories

| Repo | Name | Holds |
| --- | --- | --- |
| Playbook | `network_config-backup` | This spec, plays, tasks, inventory plugin file, `group_vars`, bootstrap. Private git remote is allowed. |
| Archive | `network_config-archive` | Redacted configs only. Not the playbook remote. Not a public remote. |

Do not put the archive remote URL in the playbook repo. The runner exports it.

## 3. What a run includes

All three are required:

- device status value is `active`
- primary IPv4 is set
- a platform is assigned (the plugin drops devices with no platform after the query)
- platform custom field `ansible_network_os` is a non-empty choice

The first two are NetBox query filters. The plugin never sends `has_platform`; it is not a filter every NetBox version advertises, so devices with no platform are removed by the plugin after the query. `ansible_network_os` lives on the platform, and the device API ignores a filter on that related field, so the play gate drops those hosts after the platform lookup.

Optional runner limits, applied as NetBox query filters by `./backup` only when the variable is non-empty:

| Variable | NetBox filter |
| --- | --- |
| `NETBOX_SITE` | site slug |
| `NETBOX_PLATFORM` | platform slug |

Empty means no filter. The playbook source does not contain site slugs or platform slugs as data. Test fixtures may use `site-a` and `platform-a` only.

`vm_query_filters` is a name that matches nothing. Do not inventory virtual machines.

Inventory plugin: `config_backup_netbox` (in `inventory_plugins/`). It queries only devices, paginates on the next URL, and groups every host under its platform slug: the group name is the slug itself. Virtual machines are never queried.

`ansible_host` is the primary IPv4 without the prefix length. `NETBOX_API` has no `/api` suffix. `NETBOX_TOKEN` comes from the environment. Neither value is written into `inventory/netbox.yml`.

## 4. Custom fields

`bootstrap_netbox.yml` ensures exactly these fields. Re-running it updates labels and choice values. It does not delete.

| Field | Object | Type | Writer | Choices |
| --- | --- | --- | --- | --- |
| `ansible_network_os` | `dcim.platform` | select | person | see below |
| `ansible_user` | `dcim.platform` | text | person | |

There are no device-level fields. The play reads the two platform fields and writes nothing back to NetBox.

`ansible_network_os` names follow this rule. If Ansible publishes an `ansible_network_os` string, the choice is that string unchanged. If it does not, the choice is the common name. Do not invent an FQCN for a product that has none.

`ansible_network_os` choices, closed set:

- `cisco.ios.ios`
- `cisco.nxos.nxos`
- `junipernetworks.junos.junos`
- `paloaltonetworks.panos.panos`
- `f5.bigip`
- `cisco.meraki`
- `velocloud`

`paloaltonetworks.panos.panos` is confirmed. The collection `paloaltonetworks.panos` ships `plugins/httpapi/panos.py` and does not ship a terminal plugin. That string is the network OS name. It is not a `network_cli` OS.

`f5.bigip`, `cisco.meraki`, and `velocloud` are dispatch keys owned by this repo. They are not Ansible `ansible_network_os` values. v1 task files for those three keys report a skip message and must not open a connection. Do not invent Dashboard, iControl, or VeloCloud API URLs.

`velocloud` is a placeholder. The later task, when someone writes it, uses `ansible_connection: local` and the `uri` module. It does not use `network_cli`. v1 does not write that task.

Empty `ansible_network_os` means the platform is not backed up.

Empty `ansible_user` means the play uses `BACKUP_USER` from the environment.

## 5. Connector

`ansible_connection` is not a NetBox field.

File name: `group_vars/<platform-slug>.yml`. The slug is the NetBox platform slug, hyphens included. Example file in git: `group_vars/example-platform.yml` containing only:

```yaml
ansible_connection: network_cli
```

Defaults when no group_vars file exists:

| `ansible_network_os` | `ansible_connection` |
| --- | --- |
| `cisco.ios.ios` | `network_cli` |
| `cisco.nxos.nxos` | `network_cli` |
| `junipernetworks.junos.junos` | `network_cli` |
| `paloaltonetworks.panos.panos` | `local` |
| `f5.bigip` | `local` |
| `cisco.meraki` | `local` |
| `velocloud` | `local` |

A site that uses NX-API sets `ansible_connection: httpapi` in that platform's `group_vars` file in that site's copy. Do not infer HTTPS from the OS string. A device that must use a different connector than the rest of its platform is out of scope. Do not add a per-device connection field.

Allowed `ansible_connection` values: `network_cli`, `httpapi`, `netconf`, `local`. Any other value fails that host.

## 6. Secrets

| Name | Where it lives | Never |
| --- | --- | --- |
| `NETBOX_TOKEN` | runner environment | repo, log |
| `BACKUP_USER` | runner environment | repo |
| `BACKUP_PASSWORD` | runner environment | repo, log, command line echo |
| `BACKUP_GIT_URL` | runner environment | embedded user or token |
| `BACKUP_GIT_TOKEN` | runner environment, only if the remote is HTTPS | URL, `.git/config`, askpass file left on disk |

Device login password is `BACKUP_PASSWORD` for every platform. Do not add a NetBox password field.

Tasks that print module args set `no_log: true` on the task that carries the password.

## 7. Tasks v1 implements

Dispatch on `ansible_network_os`. One task file per choice. Do not put more than one OS in a file.

| Choice | Collect | v1 |
| --- | --- | --- |
| `cisco.ios.ios` | `show running-config` | implement |
| `cisco.nxos.nxos` | `show running-config` | implement |
| `junipernetworks.junos.junos` | `show configuration \| display set` | implement |
| `paloaltonetworks.panos.panos` | `paloaltonetworks.panos.panos_op` cmd `show config running` | implement |
| `f5.bigip` | none | skip message, no connection |
| `cisco.meraki` | none | skip message, no connection |
| `velocloud` | none | skip message, no connection |

IOS and NX-OS use the matching `*_command` module. Junos uses `junipernetworks.junos.junos_command`. Turn the pager off before the show. Do not save the config on the device. Do not enter configuration mode.

PAN-OS uses `paloaltonetworks.panos.panos_op` on the runner. `ansible_connection` is `local`. Pass `provider` with `ip_address` from `ansible_host`, `username` from the platform user, and `password` from `BACKUP_PASSWORD`. Do not pass `api_key`. Do not use `network_cli`. The config text written to git is `stdout`, not `stdout_xml`, unless `stdout` is empty.

On connect or command failure: the host fails. Do not write a config file. Nothing is written to NetBox.

On success: write the redacted config.

`gather_facts: false`. Do not use a second play for git. Git steps are `run_once`, `delegate_to: localhost`, `connection: local`, inside the device play, so a limit does not skip them.

## 8. Redaction

Redact before the file is written. If the redacted text still matches a sensitive rule, fail that host and do not write the file.

Sensitive rules, each platform file owns its own list, v1 minimum:

- lines matching a key, password, secret, community, or phrase-secret assignment
- Junos encrypted password tokens
- PAN phash and IKE key lines

Do not log the matched line. The failure message names the host and the rule id, not the secret.

A platform with an empty rule list must not write a file. Fail closed.

## 9. Archive git

Working tree is the persistent clone of `BACKUP_GIT_URL` when that variable is set. Otherwise a directory named by `BACKUP_WORKDIR`.

Path: `<site-slug>/<inventory_hostname>.cfg`. Site slug and device name come from the inventory host. Do not sanitize them into a different name. If either contains a path separator, fail that host.

`git add` only files this task wrote. Commit only if the index differs. One commit per task, message `backup <UTC date> <limit>`, where `<limit>` is the site slug, the platform slug, or `all`.

Do not push a commit that includes a host whose redaction failed. Those hosts have no file in the commit.

Do not force-push. If the remote has no common history, stop and say so. Do not pass `--allow-unrelated-histories` in v1.

Token handling: the URL has no userinfo. If `BACKUP_GIT_TOKEN` is set, the sync script answers git and removes the helper when the push returns. Do not leave the helper on disk.

## 10. NetBox is read-only

NetBox is read-only for this playbook. The inventory plugin reads devices; the lookup plugin reads platform metadata. No task PATCHes or POSTs anything. A failed contact and a redaction leak record nothing in NetBox.

## 11. Wrapper

`./backup` is the only entry point.

It refuses to run if `NETBOX_API`, `NETBOX_TOKEN`, `BACKUP_USER`, or `BACKUP_PASSWORD` is empty. It does not print those values.

It writes a temporary inventory from `inventory/netbox.yml.example` plus the optional site and platform filters, runs `ansible-playbook backup.yml`, and deletes the temporary inventory.

Extra arguments pass through. `./backup --limit <group>` is allowed. The group name is the platform slug.

## 12. Tests

The suite runs without a device and without a token.

1. Inventory render with no site and no platform filter does not contain a site key.
2. Inventory render with `NETBOX_SITE=site-a` contains that filter and no other site.
3. A fixture config that still matches a secret rule after redaction fails and writes no file.
4. `f5.bigip`, `cisco.meraki`, and `velocloud` do not call a connection plugin and write nothing to NetBox.
5. Git commit is skipped when the redacted file is unchanged.
6. A URL with userinfo is refused.
7. `bootstrap_netbox.yml` in check mode reports the two platform fields and does not report a third.
8. The repo, excluding nothing, contains none of: a home domain, a private address, a personal login, or a device name from the owner's lab. Test fixtures use `site-a`, `device-a`, and `192.0.2.10`.

## 13. Done

Done when sections 12.1 through 12.8 pass, `./backup` against a NetBox that has one active test device with `ansible_network_os` set writes one redacted file at `<site>/<device>.cfg`, a second run makes no commit, and a `staged` device on the same platform is absent from the inventory.

Semaphore templates, schedules, and the NAS remote are not part of this repo's done gate. They are runner setup. Do not create them from this spec.

## 14. Out of scope

- Deleting NetBox fields.
- Writing any NetBox field.
- Comparing or overwriting the built-in serial.
- Storing a config hash in NetBox.
- F5, Meraki, and VeloCloud collection.
- Pushing the archive to the playbook remote.
- Opening tickets.
- Contacting any network that is not the runner's own NetBox and the devices that NetBox returned.

## 15. Decided

1. `f5.bigip`, `cisco.meraki`, and `velocloud` are placeholders. v1 does not connect to them. The strings stay as written.
2. The playbook writes nothing to NetBox.

## 16. Owner still decides

None. The PAN-OS string is confirmed as `paloaltonetworks.panos.panos`.
