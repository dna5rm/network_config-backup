#!/usr/bin/env python3
"""Meraki Dashboard backup (read path): dump org, network and device config.

Usage:
  meraki-backup.py [--api-key KEY] [--url URL] [--output DIR] [--no-archive]

Uses the read-only GET endpoints of the Meraki Dashboard API to dump the
organizations the key can see, each organization's networks, devices and
config templates, and per-network appliance VLANs, L3 firewall rules,
site-to-site VPN settings, wireless SSIDs and switch access policies into
<output>/<org name>/ as pretty-printed JSON. An existing
<output>/<org name> directory is archived to
"<output>/<org name> (YYYY-MM-DD).tgz" first and then rewritten.
--no-archive skips the tar and just rewrites the tree.

The API key comes from --api-key, else the MERAKI_API_KEY environment
variable (preferred; --api-key is visible in the process list), else a
getpass prompt when stderr is a TTY. The key is never printed.

Status and errors go to stderr; stdout stays empty on success.

NOTE: this first version does NOT redact secrets. The saved JSON can
contain PSKs and other credentials; treat the output as sensitive.
"""

import argparse
import datetime
import getpass
import itertools
import json
import os
import pathlib
import ssl
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request

# The default is the public Dashboard API host, not a tenant-specific one.
MERAKI_BASE_URL = "https://api.meraki.com/api/v1"
MERAKI_BACKUP_DIR = "meraki"


def status(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg, code=1):
    print(msg, file=sys.stderr, flush=True)
    sys.exit(code)


parser = argparse.ArgumentParser(
    description="Meraki Dashboard backup (read path)")
parser.add_argument("--api-key", default=os.environ.get("MERAKI_API_KEY", ""),
                    help="Dashboard API key (env: MERAKI_API_KEY preferred; "
                         "this shows in the process list)")
parser.add_argument("--url", default=os.environ.get("MERAKI_BASE_URL",
                                                    MERAKI_BASE_URL),
                    help="API base URL (env: MERAKI_BASE_URL, "
                         "default: %(default)s)")
parser.add_argument("--output",
                    default=os.environ.get("MERAKI_BACKUP_DIR",
                                           MERAKI_BACKUP_DIR),
                    help="backup root (env: MERAKI_BACKUP_DIR, "
                         "default: %(default)s)")
parser.add_argument("--no-archive", action="store_true",
                    help="skip archiving an existing <org name> directory")
args = parser.parse_args()
args.url = (args.url or "").strip().rstrip("/")

tty = sys.stderr.isatty()
if tty:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

api_key = args.api_key
if not api_key and tty:
    api_key = getpass.getpass("Meraki API key: ")
if not api_key:
    die("error: no API key: set MERAKI_API_KEY or pass --api-key", 2)

if not args.url:
    die("error: empty --url", 2)

ctx = ssl.create_default_context()
if hasattr(ssl, "VERIFY_X509_STRICT"):
    # Python's strict X.509 rejects CAs that browsers accept; keep chain and
    # hostname verification, drop only the strictness.
    ctx.options &= ~getattr(ssl, "VERIFY_X509_STRICT")

opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ctx))
progress = None


class Spin:
    """Braille spinner on stderr around one blocking call; plain line when piped."""

    def __init__(self, msg):
        self.msg = msg
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if not tty:
            status(self.msg)
            return self
        frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

        def spin():
            while not self._stop.is_set():
                sys.stderr.write("\r%s %s" % (next(frames), self.msg))
                sys.stderr.flush()
                self._stop.wait(0.08)

        self._thread = threading.Thread(target=spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if tty:
            sys.stderr.write("\r%s\r" % (" " * (len(self.msg) + 4)))
            sys.stderr.flush()
        return False


def is_tls_error(exc):
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, ssl.SSLError):
        return True
    return False


def api_get(path):
    """GET one authenticated API endpoint and return parsed JSON (or None for 404).

    Honors 429 Retry-After up to 5 times, then fails. Any other failure
    prints one stderr line (never the key or the body) and exits.
    """
    req = urllib.request.Request(
        args.url + path,
        headers={
            "X-Cisco-Meraki-API-Key": api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    attempts = 0
    while True:
        try:
            resp = opener.open(req, timeout=120)
            break
        except urllib.error.HTTPError as e:
            e.read()  # drain/carry on; the body is never printed
            if e.code == 404:
                return None
            if e.code == 429 and attempts < 5:
                attempts += 1
                delay = e.headers.get("Retry-After") or ""
                try:
                    wait = max(float(delay), 1.0)
                except (TypeError, ValueError):
                    wait = 1.0
                if progress is not None:
                    progress.console.print(
                        "rate limited on %s: retrying in %ss" % (path, wait))
                else:
                    with Spin("rate limited on %s: waiting %ss" % (path, wait)):
                        time.sleep(wait)
                continue
            die("error: GET %s failed: HTTP %s" % (path, e.code))
        except (ssl.SSLError, urllib.error.URLError, OSError) as e:
            if is_tls_error(e):
                die("error: TLS failure calling %s: %s" % (path, e))
            die("error: GET %s failed: %s" % (path, e))
    text = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        die("error: GET %s returned non-JSON" % path)


def api(path):
    """api_get, but a 404 is a hard failure (org-level endpoints)."""
    obj = api_get(path)
    if obj is None:
        die("error: GET %s failed: HTTP 404" % path)
    return obj


def sanitize(name):
    """Filesystem-safe path component, or None if unusable."""
    if not isinstance(name, str):
        return None
    name = name.replace("/", "-").replace("\\", "-").strip()
    if not name or name == "..":
        return None
    return name


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def skip(msg):
    """One stderr line per skipped record; above the bar when rich is on."""
    if progress is not None:
        progress.console.print(msg)
    else:
        status(msg)


def archive_existing(out_root, org_name):
    """If <out_root>/<org name> exists, tar it to "<org name> (YYYY-MM-DD).tgz"
    and delete the directory, unless --no-archive."""
    org_dir = out_root / org_name
    if not org_dir.exists():
        return
    if args.no_archive:
        with Spin("removing existing %s (no --no-archive tar)" % org_name):
            for item in sorted(org_dir.rglob("*"), reverse=True):
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    item.rmdir()
            org_dir.rmdir()
        return
    stat = org_dir.stat()
    stamp = getattr(stat, "st_birthtime", None) or stat.st_mtime
    day = datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    archive = out_root / ("%s (%s).tgz" % (org_name, day))
    with Spin("archiving existing %s" % org_name):
        with tarfile.open(archive, "w:gz") as tar:
            for item in sorted(org_dir.rglob("*")):
                tar.add(item, arcname=os.path.join(".",
                                                   os.path.relpath(item, org_dir)))
        for item in sorted(org_dir.rglob("*"), reverse=True):
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                item.rmdir()
        org_dir.rmdir()


# --- organizations ---
with Spin("getting organizations"):
    organizations = api("/organizations")
write_json(pathlib.Path(args.output) / "organizations.json", organizations)

progress = None
if tty:
    import importlib.util
    if importlib.util.find_spec("rich") is not None:
        from rich.progress import Progress
        progress = Progress()
        progress.start()

try:
    for org in (progress.track(organizations, total=len(organizations),
                               description="orgs")
                if progress is not None else organizations):
        org_id = org.get("id")
        org_name = sanitize(org.get("name"))
        if not org_name:
            skip("skip organization <id %s>: missing name" % (org_id,))
            continue
        org_dir = pathlib.Path(args.output) / org_name
        archive_existing(pathlib.Path(args.output), org_name)

        def org_get(sub):
            obj = api_get("/organizations/%s/%s" % (org_id, sub))
            if obj is not None:
                write_json(org_dir / (sub + ".json"), obj)
            return obj

        with Spin("org %s: networks/devices/templates" % org_name):
            networks = org_get("networks") or []
            org_get("devices")
            org_get("configTemplates")  # 404-safe via api_get

        bar = progress.track(networks, total=len(networks), description=org_name) \
            if progress is not None else networks
        for net in bar:
            net_id = net.get("id")
            net_name = sanitize(net.get("name"))
            if not net_name or not net_id:
                skip("skip network <id %s>: missing name or id" % (net_id,))
                continue
            net_dir = org_dir / net_name

            def net_get(sub, fname):
                obj = api_get("/networks/%s/%s" % (net_id, sub))
                if obj is not None:
                    write_json(net_dir / fname, obj)

            if progress is None:
                with Spin("network %s" % net_name):
                    net_get("appliance/vlans", "appliance/vlans.json")
                    net_get("appliance/firewall/l3FirewallRules",
                            "appliance/l3FirewallRules.json")
                    net_get("appliance/vpn/siteToSiteVpn",
                            "appliance/siteToSiteVpn.json")
                    net_get("wireless/ssids", "wireless/ssids.json")
                    net_get("switch/accessPolicies",
                            "switch/accessPolicies.json")
            else:
                net_get("appliance/vlans", "appliance/vlans.json")
                net_get("appliance/firewall/l3FirewallRules",
                        "appliance/l3FirewallRules.json")
                net_get("appliance/vpn/siteToSiteVpn",
                        "appliance/siteToSiteVpn.json")
                net_get("wireless/ssids", "wireless/ssids.json")
                net_get("switch/accessPolicies", "switch/accessPolicies.json")
            if progress is None and tty:
                status("network %s: done." % net_name)

        if progress is None and tty:
            status("org %s: done." % org_name)
finally:
    if progress is not None:
        try:
            progress.stop()
        except Exception:
            pass
