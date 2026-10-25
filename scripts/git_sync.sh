#!/usr/bin/env bash
#
# git_sync.sh: stage the archive files one run wrote, commit once, push once.
#
# Usage: git_sync.sh -- [relative-path ...]
#
# Environment:
#   BACKUP_GIT_URL      archive repository URL. Empty: keep the files in the
#                       working tree and push nothing.
#   BACKUP_WORKDIR      archive working tree. Empty: a directory named work
#                       beside this script's parent.
#   BACKUP_GIT_TOKEN    HTTPS push token. Answered from the environment by a
#                       private askpass helper that is removed when the script
#                       exits; it is never written into the URL or on disk
#                       anywhere else.
#   BACKUP_GIT_USER     optional HTTPS token username, default git.
#   NETBOX_SITE         when set, the commit message carries the site slug.
#   NETBOX_PLATFORM     otherwise the platform slug, otherwise 'all'.
#
# Behaviour required by the spec:
#   - URLs carrying userinfo (user or user:pass before the host) are refused.
#   - Only the files named on the command line are staged.
#   - The commit is made only when the staging area differs.
#   - No force-push and no unrelated-history merge: when the remote has no
#     common history with this working tree, the script stops.
set -euo pipefail

if [ "${1:-}" != "--" ]; then
  echo "git_sync: expected 'git_sync.sh -- [relative-path ...]'." >&2
  exit 2
fi
shift

url="${BACKUP_GIT_URL:-}"
workdir="${BACKUP_WORKDIR:-}"
if [ -z "$workdir" ]; then
  workdir="$(cd -- "$(dirname -- "$0")/.." && pwd)/work"
fi

# Refuse any userinfo segment in the authority part of the URL. This covers
# scheme URLs (https://user@host/x) and SCP-style URLs (user@host:repo).
if [ -n "$url" ]; then
  authority=""
  schemeless="${url#*://}"
  if [ "$schemeless" != "$url" ]; then
    authority="${schemeless%%[/]*}"
  else
    authority="${url%%:*}"
    authority="${authority%%[/]*}"
  fi
  case "$authority" in
    *@*)
      echo "git_sync: refusing a git URL that carries credentials in the URL." >&2
      exit 2
      ;;
  esac
fi

# HTTPS token auth: a private askpass helper answers git from the environment
# at run time and is removed on exit. The heredoc delimiter is quoted so the
# helper text is stored literally; the token is expanded only inside the
# helper's own process.
if [ -n "${BACKUP_GIT_TOKEN:-}" ] && [ -n "$url" ]; then
  askpass_file="$(mktemp "${TMPDIR:-/tmp}/config-backup-askpass.XXXXXX")"
  chmod 700 "$askpass_file"
  cat >"$askpass_file" <<'EOF'
#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\n' "${BACKUP_GIT_USER:-git}" ;;
  *) printf '%s\n' "$BACKUP_GIT_TOKEN" ;;
esac
EOF
  export GIT_ASKPASS="$askpass_file"
  export GIT_TERMINAL_PROMPT=0
  trap 'rm -f "$askpass_file"' EXIT
fi

if [ -z "$url" ]; then
  echo "git_sync: BACKUP_GIT_URL is empty; the files stay in $workdir and nothing is pushed."
  exit 0
fi

# Validate the paths this run claims to have written.
for path in "$@"; do
  case "$path" in
    /*|*..*)
      echo "git_sync: refusing archive path '$path'." >&2
      exit 2
      ;;
  esac
  if [ ! -f "$workdir/$path" ]; then
    echo "git_sync: expected archive file '$workdir/$path' was not written." >&2
    exit 2
  fi
done

git_commit() {
  git -C "$workdir" \
    -c "user.name=${BACKUP_GIT_NAME:-Config Backup}" \
    -c "user.email=${BACKUP_GIT_EMAIL:-config-backup@localhost.invalid}" \
    commit "$@"
}

mkdir -p "$workdir"

if [ ! -d "$workdir/.git" ]; then
  if [ -n "$(ls -A -- "$workdir")" ]; then
    # Files were written before the first sync: init in place and register
    # the remote. Common history is checked below before anything is added.
    git -C "$workdir" init --quiet
    git -C "$workdir" symbolic-ref HEAD refs/heads/main
    git -C "$workdir" remote add origin -- "$url"
  else
    git clone --quiet -- "$url" "$workdir"
  fi
fi

if [ -z "$(git -C "$workdir" remote get-url origin 2>/dev/null || true)" ]; then
  git -C "$workdir" remote add origin -- "$url"
fi

branch="$(git -C "$workdir" symbolic-ref --quiet --short HEAD || echo main)"

git fetch --quiet "$workdir" 2>/dev/null || git -C "$workdir" fetch --quiet origin

local_head=""
if git -C "$workdir" rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
  local_head="$(git -C "$workdir" rev-parse --verify HEAD)"
fi
remote_ref="refs/remotes/origin/$branch"
remote_head=""
if git -C "$workdir" rev-parse --verify --quiet "$remote_ref" >/dev/null 2>&1; then
  remote_head="$(git -C "$workdir" rev-parse --verify "$remote_ref")"
fi

# Stop rather than merge unrelated histories (no force, no allow-unrelated).
if [ -n "$local_head" ] && [ -n "$remote_head" ]; then
  if ! git -C "$workdir" merge-base --is-ancestor "$remote_head" "$local_head" >/dev/null 2>&1; then
    echo "git_sync: no common history with origin/$branch; stopping. Nothing was force-pushed." >&2
    exit 3
  fi
  git -C "$workdir" pull --quiet --ff-only origin "$branch" \
    || { echo "git_sync: the working tree diverged from origin/$branch; reconcile by hand. Nothing was force-pushed." >&2; exit 3; }
elif [ -z "$local_head" ] && [ -n "$remote_head" ]; then
  echo "git_sync: origin/$branch has history but the working tree has none; reconcile by hand. Nothing was force-pushed." >&2
  exit 3
fi

if [ "$#" -gt 0 ]; then
  git -C "$workdir" add -- "$@"
fi

limit="all"
if [ -n "${NETBOX_SITE:-}" ]; then
  limit="$NETBOX_SITE"
elif [ -n "${NETBOX_PLATFORM:-}" ]; then
  limit="$NETBOX_PLATFORM"
fi

if ! git -C "$workdir" diff --cached --quiet; then
  git_commit --quiet --message "backup $(date -u '+%Y-%m-%dT%H:%M:%SZ') $limit"
  echo "git_sync: committed backup $(date -u '+%Y-%m-%d') $limit"
fi

git -C "$workdir" push --quiet origin HEAD \
  || { echo "git_sync: push was rejected; reconcile by hand. Nothing was force-pushed." >&2; exit 3; }
echo "git_sync: pushed archive."