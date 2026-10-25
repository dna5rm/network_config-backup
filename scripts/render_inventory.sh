#!/usr/bin/env bash
#
# render_inventory.sh: copy an inventory template and insert the optional
# site and platform runner filters in place of the anchor comment.
#
# Usage: render_inventory.sh SRC DST
#
# NETBOX_SITE and NETBOX_PLATFORM are read from the environment. Empty means
# no filter. Values that are not NetBox slugs are refused so a hostile
# environment variable cannot inject YAML into the rendered inventory.
set -euo pipefail

src="${1:?render_inventory: source template required}"
dst="${2:?render_inventory: destination path required}"

is_slug() {
  case "$1" in
    ''|*[!A-Za-z0-9_-]*) return 1 ;;
    *) return 0 ;;
  esac
}

filters=""
if [ -n "${NETBOX_SITE:-}" ]; then
  if ! is_slug "$NETBOX_SITE"; then
    echo "render_inventory: refusing NETBOX_SITE: not a NetBox slug." >&2
    exit 2
  fi
  filters+="  - site: '${NETBOX_SITE}'"$'\n'
fi
if [ -n "${NETBOX_PLATFORM:-}" ]; then
  if ! is_slug "$NETBOX_PLATFORM"; then
    echo "render_inventory: refusing NETBOX_PLATFORM: not a NetBox slug." >&2
    exit 2
  fi
  filters+="  - platform: '${NETBOX_PLATFORM}'"$'\n'
fi

# The anchor line sits inside device_query_filters. Without filters it is
# dropped; with filters it is replaced by the limit lines.
awk -v repl="$filters" '
  /anchor: config-backup-runner-filters/ { if (repl != "") printf "%s", repl; next }
  { print }
' "$src" > "$dst"