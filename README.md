# network_config-backup

Nightly device configuration backup driven by NetBox membership. A device
is backed up when NetBox says it is active, it has a primary IPv4, and its
platform custom field `ansible_network_os` holds one of the backed-up
choices. Nothing is filtered on site or role in the committed inventory;
the wrapper applies optional site and platform limits as NetBox query
filters when their variables are non-empty.

The archive working tree lives outside this repository. Files land at
`<site-slug>/<device-name>.cfg` at the archive root (no `archive/`
directory). The playbook repo holds no secret, no site name, no device
name, no address and no login: every credential comes from the environment
of the runner.

## Layout

| Path | Purpose |
| --- | --- |
| `backup` | Wrapper and only entry point; refuses to run without credentials, renders a temporary inventory and runs `ansible-playbook backup.yml`. |
| `backup.yml` | The device play: gate, dispatch on `ansible_network_os`, redact, archive, NetBox writeback, git sync. |
| `bootstrap_netbox.yml` | One-time creation/update of the six NetBox custom fields. Never run by the nightly job. |
| `inventory/netbox.yml.example` | NetBox inventory template; `./backup` renders the real inventory from it. |
| `inventory/empty.yml` | Empty inventory for offline tooling and syntax checks. |
| `group_vars/all.yml` | Connection defaults per OS, resolved from NetBox platform metadata. |
| `group_vars/example-platform.yml` | Example per-platform connection override. |
| `lookup_plugins/config_backup_platforms.py` | Read-only fetch of the platform metadata (custom fields live on the platform, which the inventory plugin does not carry into hostvars). |
| `filter_plugins/*_redact.py` | Per-platform redaction, leak backstop and version parse rules. |
| `tasks/ios.yml` | cisco.ios.ios collection tasks. |
| `tasks/nxos.yml` | cisco.nxos.nxos collection tasks. |
| `tasks/junos.yml` | junipernetworks.junos.junos collection tasks. |
| `tasks/panos.yml` | paloaltonetworks.panos.panos_op over HTTPS from the runner. |
| `tasks/f5.yml`, `tasks/meraki.yml`, `tasks/velocloud.yml` | Placeholders: set `validation_state` `no_task` and never open a connection. |
| `tasks/_netbox_patch.yml` | Shared NetBox writeback (read, merge, PATCH device custom fields only). |
| `tasks/_archive_write.yml` | Shared archive path guard and file write. |
| `tasks/_no_task.yml` | Shared placeholder record. |
| `scripts/render_inventory.sh` | Renders the temporary inventory, inserting site/platform filters. |
| `scripts/git_sync.sh` | Stages this run's files, commits once, pushes once; no force, no unrelated-history merge. |
| `tests/` | Offline suite; needs no device and no token. |

## Environment

| Variable | Used for | Required |
| --- | --- | --- |
| `NETBOX_API` | NetBox URL without the `/api` suffix | yes |
| `NETBOX_TOKEN` | NetBox read/write of device custom fields only | yes |
| `BACKUP_USER` | Device login when the platform user custom field is empty | yes |
| `BACKUP_PASSWORD` | Device login and enable password for every platform | yes |
| `NETBOX_SITE` | Optional runner limit: site slug | no |
| `NETBOX_PLATFORM` | Optional runner limit: platform slug | no |
| `BACKUP_GIT_URL` | Archive repository URL, never with credentials embedded | no |
| `BACKUP_GIT_TOKEN` | HTTPS push token, answered from the environment at run time | no |
| `BACKUP_WORKDIR` | Archive working tree; the sync script clones the remote into it | no |

## Device membership

NetBox-side: `device_query_filters` keep `status: active` and
`has_primary_ip: 'true'`. Play-side: the gate drops devices whose platform
custom field `ansible_network_os` is empty or unknown, and fails a device
whose connector is not one of `network_cli`, `httpapi`, `netconf`, `local`
(recorded as `validation_state` `no_task`). Devices without a primary IPv4
compose an empty `ansible_host` and are dropped the same way. Nothing ever
contacts a device that did not qualify.

## Validation states

- `current`: collected, redacted and parsed.
- `unreachable`: contact failed; `observed_version` and `observed_at` untouched, no file written.
- `unparsed`: the device answered but the version did not parse or the redacted text still matched a secret rule; `observed_version` untouched.
- `no_task`: placeholder platforms and connectors outside the allowed set.

A redaction that leaves a secret fails the host: the raw configuration is
never written, the archive file is not created, and the run exits non-zero.
The failure message names the host and the rule id, never the secret.

## Connector defaults

| `ansible_network_os` | `ansible_connection` |
| --- | --- |
| `cisco.ios.ios` | `network_cli` |
| `cisco.nxos.nxos` | `network_cli` |
| `junipernetworks.junos.junos` | `network_cli` |
| `paloaltonetworks.panos.panos` | `local` (panos_op over HTTPS from the runner) |
| `f5.bigip` | `local` (placeholder, no connection) |
| `cisco.meraki` | `local` (placeholder, no connection) |
| `velocloud` | `local` (placeholder, no connection) |

A platform that needs a different connector ships a copy of
`group_vars/example-platform.yml` as `group_vars/<platform-slug>.yml` in
that deployment of the tree.

## Running

```
export NETBOX_API=... NETBOX_TOKEN=...
export BACKUP_USER=... BACKUP_PASSWORD=...
./backup                         # no limit
./backup --limit <platform-slug> # group names are raw NetBox slugs
```

NetBox writeback happens per device after a successful collect (and as
`validation_state` after a failed one): only `observed_version`,
`observed_at` and `validation_state` are written. `observed_at` is UTC
ISO-8601. The archive commit message is `backup <UTC date> <limit>` where
the limit is the site slug, the platform slug, or `all`.

## Bootstrap fields

```
ansible-playbook -i inventory/empty.yml --check bootstrap_netbox.yml   # report only
NETBOX_API=... NETBOX_TOKEN=... ansible-playbook bootstrap_netbox.yml  # create/update
```

Creates or updates exactly these six fields and never deletes: platform
`ansible_network_os` (select, the seven OS choices), platform `ansible_user`
(text), device `observed_version` (text), device `observed_at` (datetime),
device `validation_state` (select: current, unreachable, unparsed, no_task)
and device `desired_version` (text).

## Tests

```
python3 -m unittest discover -s tests -v
```

Run without a device and without a token. The inventory, bootstrap, redaction,
git sync, hygiene and no-task flow tests cover spec sections 12.1 through
12.9; the bootstrap check-mode test needs `ansible-playbook` and is skipped
with a reason when it is missing.