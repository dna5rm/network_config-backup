#!/usr/bin/env python3
"""VeloCloud VCO backup (read path): replaces the shell script's read path.

Usage:
  vco-backup.py [--url URL] [--username USER] [--password PW] [--output DIR]
                [--no-archive]

Logs in to the VeloCloud portal, then dumps the enterprise, its profiles,
profile modules, edges, per-edge configuration stacks, network segments,
services and object groups into <output>/<enterprise name>/ as
pretty-printed JSON. An existing <output>/<enterprise name> directory is
archived to "<output>/<enterprise name> (YYYY-MM-DD).tgz" first and then
rewritten. With --no-archive that tar-and-delete is skipped and the files
are written in place, so a caller backed by git sees the diff instead.

Credentials come from --username/--password, else the VCO_USERNAME /
VCO_PASSWORD environment variables (preferred; --password is visible in
the process list), else a prompt when stderr is a TTY. The password is
never printed.

Status and errors go to stderr; stdout stays empty on success.
"""

import argparse
import datetime
import getpass
import http.cookiejar
import itertools
import json
import os
import pathlib
import ssl
import sys
import tarfile
import threading
import urllib.error
import urllib.parse
import urllib.request

# No orchestrator host is built in. Set VCO_URL or pass --url.
VCO_BACKUP_DIR = "vco-backup"
SESSION_COOKIE_NAME = "velocloud.session"


def status(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg, code=1):
    print(msg, file=sys.stderr, flush=True)
    sys.exit(code)


parser = argparse.ArgumentParser(
    description="VeloCloud VCO backup (read path)")
parser.add_argument("--url", default=os.environ.get("VCO_URL", ""),
                    help="portal REST base URL (env: VCO_URL, required)")
parser.add_argument("--username", default=os.environ.get("VCO_USERNAME", ""),
                    help="portal username (env: VCO_USERNAME)")
parser.add_argument("--password", default=os.environ.get("VCO_PASSWORD", ""),
                    help="portal password (env: VCO_PASSWORD preferred; "
                         "this shows in the process list)")
parser.add_argument("--output",
                    default=os.environ.get("VCO_BACKUP_DIR", VCO_BACKUP_DIR),
                    help="backup root (env: VCO_BACKUP_DIR, default: %(default)s)")
parser.add_argument("--no-archive", action="store_true",
                    help="skip the tar-and-delete of an existing enterprise "
                         "directory and write the files in place")
args = parser.parse_args()
args.url = (args.url or "").strip().rstrip("/")
if not args.url:
    die("error: set VCO_URL or pass --url", 2)

tty = sys.stderr.isatty()
if tty:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

username = args.username.strip()
password = args.password
if not username and tty:
    username = input("VCO username: ").strip()
if not password and tty:
    password = getpass.getpass("VCO password: ")
if not username or not password:
    die("error: no credentials: set VCO_USERNAME and VCO_PASSWORD", 2)

ctx = ssl.create_default_context()
if hasattr(ssl, "VERIFY_X509_STRICT"):
    # Python's strict X.509 rejects corporate CAs that browsers accept;
    # keep chain and hostname verification, drop only the strictness.
    ctx.options &= ~getattr(ssl, "VERIFY_X509_STRICT")


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
        frames = itertools.cycle("⠻⠹⠸⠼⠴⠶⠷⠯⠻")

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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


base = args.url.rstrip("/")
host = urllib.parse.urlsplit(base).hostname
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    urllib.request.HTTPSHandler(context=ctx),
    NoRedirect(),
)
progress = None


def is_tls_error(exc):
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, ssl.SSLError):
        return True
    return False


def api(path, body=None):
    """POST one authenticated API call and return the parsed JSON; any
    failure prints one stderr line and exits."""
    req = urllib.request.Request(
        base + "/" + path,
        data=json.dumps({} if body is None else body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = opener.open(req, timeout=120)
    except urllib.error.HTTPError as e:
        e.read()
        die("error: %s failed: HTTP %s" % (path, e.code))
    except (ssl.SSLError, urllib.error.URLError, OSError) as e:
        if is_tls_error(e):
            die("error: TLS failure calling %s: %s" % (path, e))
        die("error: %s failed: %s" % (path, e))
    text = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        die("error: %s returned non-JSON" % path)


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
    if isinstance(obj, str):
        # Non-JSON payload: keep the raw text rather than lose it.
        path.write_text(obj, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def skip(msg):
    """One stderr line per skipped record; above the bar when rich is on."""
    if progress is not None:
        progress.console.print(msg)
    else:
        status(msg)


has_session = False
try:
    # --- login: no redirects; 200 or 302 with Set-Cookie velocloud.session ---
    with Spin("logging in to %s" % host):
        login_req = urllib.request.Request(
            base + "/login/enterpriseLogin",
            data=json.dumps({"username": username, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = opener.open(login_req, timeout=120)
            code = resp.getcode()
        except urllib.error.HTTPError as e:
            resp = e
            code = e.code
        except (ssl.SSLError, urllib.error.URLError, OSError) as e:
            if is_tls_error(e):
                die("error: TLS failure during login: %s" % e)
            die("error: login failed: %s" % e)
        if code not in (200, 302):
            print("error: login failed: HTTP %s" % code, file=sys.stderr)
            sys.exit(1)
    set_cookie = resp.headers.get("Set-Cookie", "")
    pair = None
    for part in set_cookie.split(","):
        first = part.strip().split(";")[0]
        if first.lower().startswith(SESSION_COOKIE_NAME.lower() + "="):
            pair = first
            break
    if not pair:
        print("error: login failed: no %s cookie issued" % SESSION_COOKIE_NAME,
              file=sys.stderr)
        sys.exit(1)
    name, _, value = pair.partition("=")
    jar.set_cookie(http.cookiejar.Cookie(
        version=0, name=name, value=value,
        port=None, port_specified=False,
        domain=host, domain_specified=False, domain_initial_dot=False,
        path="/", path_specified=True,
        secure=base.startswith("https"), expires=None, discard=True,
        comment=None, comment_url=None, rest={},
    ))
    has_session = True

    # --- enterprise and archive-existing tree ---
    with Spin("getting enterprise"):
        enterprise = api("enterprise/getEnterprise")
    ent_id = enterprise.get("id")
    ent_name = sanitize(enterprise.get("name"))
    if not ent_name:
        die("error: skip enterprise <id %s>: missing name" % (ent_id,))

    out_root = pathlib.Path(args.output)
    ent_dir = out_root / ent_name
    # With --no-archive the existing tree is left in place and rewritten:
    # a git-backed caller sees the diff instead of a tarball.
    if ent_dir.exists() and not args.no_archive:
        stat = ent_dir.stat()
        stamp = getattr(stat, "st_birthtime", None) or stat.st_mtime
        day = datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
        archive = out_root / ("%s (%s).tgz" % (ent_name, day))
        with Spin("archiving existing %s" % ent_dir.name):
            with tarfile.open(archive, "w:gz") as tar:
                for item in sorted(ent_dir.rglob("*")):
                    tar.add(item, arcname=os.path.join(".", os.path.relpath(item, ent_dir)))
            for item in sorted(ent_dir.rglob("*"), reverse=True):
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    item.rmdir()
            ent_dir.rmdir()

    # --- static record dumps ---
    with Spin("getting enterprise profiles"):
        profiles = api("enterprise/getEnterpriseConfigurations")
    write_json(ent_dir / "profiles.json", profiles)
    with Spin("getting enterprise edges"):
        edges = api("enterprise/getEnterpriseEdges")
    write_json(ent_dir / "edges.json", edges)

    # --- profile and edge loops: ONE rich bar when stderr is a TTY ---
    progress = None
    if tty:
        import importlib.util
        if importlib.util.find_spec("rich") is not None:
            from rich.progress import Progress
            progress = Progress()
            progress.start()

    for profile in (progress.track(profiles, total=len(profiles), description="profiles")
                    if progress is not None else profiles):
        pid = profile.get("id")
        profile_name = sanitize(profile.get("name"))
        if not profile_name:
            skip("skip profile <id %s>: missing name" % (pid,))
            continue
        if progress is None:
            with Spin("profile %s" % profile_name):
                modules = api("configuration/getConfigurationModules",
                              {"configurationId": pid})
        else:
            modules = api("configuration/getConfigurationModules",
                          {"configurationId": pid})
        for module in modules:
            mname = sanitize(module.get("name"))
            if not mname:
                skip("skip module <id %s>: missing name" % (module.get("id"),))
                continue
            write_json(ent_dir / profile_name / (mname + ".json"), module)
        if progress is None and tty:
            status("profile %s: done." % profile_name)

    for edge in (progress.track(edges, total=len(edges), description="edges")
                 if progress is not None else edges):
        eid = edge.get("id")
        edge_name = sanitize(edge.get("name"))
        if not edge_name:
            skip("skip edge <id %s>: missing name" % (eid,))
            continue
        if progress is None:
            with Spin("edge %s" % edge_name):
                stack = api("edge/getEdgeConfigurationStack", {"edgeId": eid})
        else:
            stack = api("edge/getEdgeConfigurationStack", {"edgeId": eid})
        prof_name = "no-profile"
        if isinstance(stack, list) and len(stack) > 1 and isinstance(stack[1], dict):
            prof_name = sanitize(stack[1].get("name")) or "no-profile"
        modules = stack[0].get("modules", []) \
            if isinstance(stack, list) and stack and isinstance(stack[0], dict) else []
        for module in modules:
            mname = sanitize(module.get("name"))
            if not mname:
                skip("skip module <id %s>: missing name" % (module.get("id"),))
                continue
            write_json(ent_dir / prof_name / edge_name / (mname + ".json"), module)
        if progress is None and tty:
            status("edge %s: done." % edge_name)

    if progress is not None:
        progress.stop()
        progress = None

    # --- remaining record dumps ---
    with Spin("getting network segments"):
        write_json(ent_dir / "network_segments.json",
                   api("enterprise/getEnterpriseNetworkSegments"))
    with Spin("getting services"):
        write_json(ent_dir / "services.json",
                   api("enterprise/getEnterpriseServices"))
    with Spin("getting object groups"):
        write_json(ent_dir / "object_groups.json",
                   api("enterprise/getObjectGroups"))

finally:
    if progress is not None:
        try:
            progress.stop()
        except Exception:
            pass
    if has_session:
        # Always POST logout; ignore errors. Never print the cookie.
        try:
            logout_req = urllib.request.Request(
                base + "/logout",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            opener.open(logout_req, timeout=30).read()
        except Exception:
            pass
