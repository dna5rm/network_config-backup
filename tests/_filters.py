"""Shared access to the version parsers of the four platform modules."""

from filter_plugins.ios_redact import ios_version
from filter_plugins.junos_redact import junos_version
from filter_plugins.nxos_redact import nxos_version
from filter_plugins.panos_redact import panos_version

FILTERS = {
    "ios": ios_version,
    "nxos": nxos_version,
    "junos": junos_version,
    "panos": panos_version,
}

PLATFORMS = ("ios", "nxos", "junos", "panos")