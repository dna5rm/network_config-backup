'''NetBox device inventory for network_config-backup.

Plugin name: config_backup_netbox. The plugin reads NETBOX_API and
NETBOX_TOKEN from the environment and queries /api/dcim/devices/ with the
device_query_filters set in the inventory source. Virtual machines are
never queried; the vm_query_filters option is accepted and ignored.

Membership: the query keeps devices whose status is active and which have
a primary IPv4. The plugin drops devices with no platform after the query,
because has_platform is not a filter every NetBox version advertises.
Devices with an empty name are dropped too. Each surviving host lands in
one group named after its platform slug and carries ansible_host (the
primary IPv4 without the prefix length), nb_site_slug and
nb_platform_slug.

Errors name the HTTP status and a short response body only. The token is
never printed.
'''

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

DOCUMENTATION = r'''
inventory: config_backup_netbox
plugin_type: inventory
short_description: NetBox device inventory for config backups
description:
  - Queries the NetBox device list with device_query_filters and follows the
    paginated next URL until the page is exhausted. Drops devices whose
    platform is null or whose name is empty. Groups each host under its
    platform slug. Virtual machines are never queried.
options:
  device_query_filters:
    description: NetBox query filters sent on the device list request.
    type: list
    elements: dict
    default: []
  vm_query_filters:
    description: Accepted so the inventory source stays renderable. Ignored.
    type: list
    elements: dict
    default: []
  timeout:
    description: Per-request timeout in seconds.
    type: int
    default: 180
  validate_certs:
    description: Verify the NetBox TLS certificate.
    type: boolean
    default: true
'''

_PAGE_LIMIT = '500'
_ERROR_BODY_CHARS = 200


def platform_slug(device):
    '''Return the platform slug of a NetBox device record, or an empty one.

    Accepts the platform object from the device payload and a bare slug
    string, which some plugin versions hand through instead.
    '''
    platform = (device or {}).get('platform')
    if isinstance(platform, dict):
        return platform.get('slug') or ''
    if isinstance(platform, str):
        return platform
    return ''


def query_pairs(filters):
    '''Flatten the runner filter list into NetBox query string pairs.

    Each entry is a one-key mapping; bools become true/false strings. The
    has_platform key is dropped unconditionally: it is not a query filter
    every NetBox version advertises, and the plugin itself removes devices
    with no platform.
    '''
    pairs = []
    for entry in filters or []:
        if not isinstance(entry, dict):
            continue
        for key, value in entry.items():
            if key == 'has_platform':
                continue
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            pairs.append((key, str(value)))
    return pairs


try:
    from ansible.errors import AnsibleError
    from ansible.plugins.inventory import BaseInventoryPlugin
except ImportError:
    AnsibleError = None
    BaseInventoryPlugin = None


if BaseInventoryPlugin is not None:

    class InventoryModule(BaseInventoryPlugin):
        '''NetBox query-first inventory plugin for config backups.'''

        NAME = 'config_backup_netbox'

        def parse(self, inventory, loader, path, cache=False):
            super().parse(inventory, loader, path, cache)

            api = os.environ.get('NETBOX_API') or ''
            token = os.environ.get('NETBOX_TOKEN') or ''
            if not api or not token:
                raise AnsibleError(
                    'config_backup_netbox: set NETBOX_API and NETBOX_TOKEN '
                    'in the environment.')

            verify = self.get_option('validate_certs')
            timeout = int(self.get_option('timeout'))
            context = None
            if verify is False:
                context = ssl._create_unverified_context()

            pairs = query_pairs(self.get_option('device_query_filters'))
            pairs.append(('limit', _PAGE_LIMIT))
            url = (api.rstrip('/') + '/api/dcim/devices/?'
                   + urllib.parse.urlencode(pairs))

            devices = []
            while url:
                payload = self._fetch(url, token, timeout, context)
                devices.extend(payload.get('results') or [])
                url = payload.get('next') or ''

            self._populate(devices)

        def _fetch(self, url, token, timeout, context):
            request = urllib.request.Request(
                url,
                headers={
                    'Authorization': 'Token ' + token,
                    'Accept': 'application/json',
                    'User-Agent': 'config_backup_netbox inventory',
                })
            try:
                with urllib.request.urlopen(
                        request, timeout=timeout, context=context) as response:
                    return json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                body = exc.read(_ERROR_BODY_CHARS).decode('utf-8', 'replace')
                raise AnsibleError(
                    'config_backup_netbox: NetBox returned HTTP %s: %s'
                    % (exc.code, body.strip())) from exc
            except urllib.error.URLError as exc:
                raise AnsibleError(
                    'config_backup_netbox: cannot reach NetBox: %s'
                    % (getattr(exc, 'reason', exc),)) from exc

        def _populate(self, devices):
            for device in devices:
                name = device.get('name') or ''
                slug = platform_slug(device)
                if not name or not slug:
                    continue
                if slug not in self.inventory.groups:
                    self.inventory.add_group(slug)
                self.inventory.add_host(name, group=slug)

                address = ''
                primary_ip4 = device.get('primary_ip4')
                if isinstance(primary_ip4, dict):
                    address = primary_ip4.get('address') or ''
                if address:
                    self.inventory.set_variable(
                        name, 'ansible_host', address.split('/')[0])

                site = device.get('site') or {}
                if isinstance(site, dict):
                    self.inventory.set_variable(
                        name, 'nb_site_slug', site.get('slug') or '')
                self.inventory.set_variable(name, 'nb_platform_slug', slug)
