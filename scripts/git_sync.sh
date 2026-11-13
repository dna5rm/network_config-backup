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

# Validate the paths this run claims to have written. A regular file is
# staged by name. An existing directory (the VeloCloud portal dump) is
# staged with 'git add -A' so removals inside it land in the index too.
# Absolute paths and anything containing '..' stay refused: only plain
# relative paths under the archive working tree are accepted.
is_archive_directory() {
  [ -d "$workdir/$1" ] && [ ! -L "$workdir/$1" ]
}

for path in "$@"; do
  case "$path" in
    /*|*..*)
      echo "git_sync: refusing archive path '$path'." >&2
      exit 2
      ;;
  esac
  if is_archive_directory "$path"; then
    continue
  fi
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

if git -C "$workdir" remote get-url origin >/dev/null 2>&1; then
  current_url="$(git -C "$workdir" remote get-url origin)"
  if [ "$current_url" != "$url" ]; then
    git -C "$workdir" remote set-url origin -- "$url"
  fi
else
  git -C "$workdir" remote add origin -- "$url"
fi

branch="$(git -C "$workdir" symbolic-ref --quiet --short HEAD || echo main)"

git -C "$workdir" fetch --prune --quiet origin 2>/dev/null || true

local_head=""
if git -C "$workdir" rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
  local_head="$(git -C "$workdir" rev-parse --verify HEAD)"
fi

# The remote's history is decided by the live remote, not by a leftover
# tracking ref from an earlier run against a different (stale) archive.
remote_ref="refs/remotes/origin/$branch"
remote_head="$(git -C "$workdir" ls-remote --heads origin "$branch" || true)"

if [ -z "$remote_head" ]; then
  # The remote has no branches (a recreated-empty archive). Drop any stale
  # tracking ref left over from the previous remote and continue: the local
  # files, with or without existing commits, get committed and pushed on top.
  if git -C "$workdir" rev-parse --verify --quiet "$remote_ref" >/dev/null 2>&1; then
    git -C "$workdir" update-ref -d "$remote_ref"
  fi
elif [ -z "$local_head" ]; then
  # The remote has history but the working tree has no commits yet. Check that
  # remote branch out in place without deleting the files the play just wrote.
  fetched_head="$(printf '%s\n' "$remote_head" | awk '{print $1}' | head -n1)"
  git -C "$workdir" checkout --quiet -B "$branch" "refs/remotes/origin/$branch" \
    2>/dev/null \
    || git -C "$workdir" checkout --quiet -f -B "$branch" "refs/remotes/origin/$branch"
  echo "git_sync: adopted existing origin/$branch history at $fetched_head"
fi

# Stop rather than merge unrelated histories (no force, no allow-unrelated).
if [ -n "$local_head" ] && [ -n "$remote_head" ]; then
  remote_commit="$(printf '%s\n' "$remote_head" | awk '{print $1}' | head -n1)"
  if ! git -C "$workdir" merge-base --is-ancestor "$remote_commit" "$local_head" >/dev/null 2>&1; then
    echo "git_sync: no common history with origin/$branch; stopping. Nothing was force-pushed." >&2
    exit 3
  fi
  git -C "$workdir" pull --quiet --ff-only origin "$branch" \
    || { echo "git_sync: the working tree diverged from origin/$branch; reconcile by hand. Nothing was force-pushed." >&2; exit 3; }
fi

# The portal directory was renamed from vco-backup to velocloud. The pull above
# restores the old tree from origin, so drop it here unless this run is staging
# that name on purpose.
drop_old=1
for path in "$@"; do
  case "$path" in
    vco-backup|vco-backup/*) drop_old=0 ;;
  esac
done
if [ "$drop_old" -eq 1 ] && git -C "$workdir" ls-files -- vco-backup | grep -q .; then
  git -C "$workdir" rm -r --quiet --ignore-unmatch -- vco-backup
fi

if [ "$#" -gt 0 ]; then
  git -C "$workdir" add -- "$@"
  for path in "$@"; do
    if is_archive_directory "$path"; then
      git -C "$workdir" add -A -- "$path"
    fi
  done
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