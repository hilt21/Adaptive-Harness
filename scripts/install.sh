#!/bin/sh

set -eu

repository="${HARNESS_RELEASE_REPOSITORY:-hilt21/Adaptive-Harness}"
release_base="${HARNESS_RELEASE_BASE:-https://github.com/${repository}/releases/download}"
install_dir="${HARNESS_INSTALL_DIR:-${HOME}/.local/bin}"
version="${HARNESS_VERSION:-}"
managed_profile=
staged_runtime=
staged_profile=
created_manifest_link=0
install_committed=0
profile_changed=0
profile_existed=0
profile_backup=
launcher_changed=0
launcher_existed=0
launcher_backup=
current_changed=0
previous_changed=0
old_current_target=
old_previous_target=
new_runtime=
new_runtime_target=
data_root_existed=0
runtime_parent_existed=0
runtime_slots_existed=0
data_root=
runtime_parent=
runtime_slots=
install_lock=
install_lock_acquired=0
candidate_lock=
launcher_installed=

fail() {
  printf 'harness installer: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

detect_target() {
  system=$(uname -s)
  machine=$(uname -m)
  case "$system" in
    Darwin) platform=macos ;;
    Linux)
      platform=linux
      if ! getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
        fail "self-contained builds require glibc; use the Python package on musl"
      fi
      ;;
    *) fail "unsupported operating system: $system" ;;
  esac
  case "$machine" in
    arm64|aarch64) architecture=arm64 ;;
    x86_64|amd64) architecture=x86_64 ;;
    *) fail "unsupported CPU architecture: $machine" ;;
  esac
  printf '%s-%s\n' "$platform" "$architecture"
}

resolve_version() {
  latest=$(curl -fsSIL -o /dev/null -w '%{url_effective}' \
    "https://github.com/${repository}/releases/latest")
  tag=${latest##*/}
  case "$tag" in
    v*) printf '%s\n' "${tag#v}" ;;
    *) fail "could not resolve the latest release version" ;;
  esac
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    fail "sha256sum or shasum is required to verify the release"
  fi
}

validate_semver() {
  awk -v version="$1" '
  function valid_identifiers(value, prerelease, n, parts, idx) {
    n = split(value, parts, ".")
    if (n < 1) return 0
    for (idx = 1; idx <= n; idx += 1) {
      if (parts[idx] == "" || parts[idx] !~ /^[0-9A-Za-z-]+$/) return 0
      if (prerelease && parts[idx] ~ /^[0-9]+$/ &&
          length(parts[idx]) > 1 && substr(parts[idx], 1, 1) == "0") return 0
    }
    return 1
  }
  BEGIN {
    base = version
    plus = index(base, "+")
    if (plus > 0) {
      build = substr(base, plus + 1)
      base = substr(base, 1, plus - 1)
      if (build == "" || index(build, "+") > 0 ||
          !valid_identifiers(build, 0)) exit 1
    }
    dash = index(base, "-")
    if (dash > 0) {
      prerelease = substr(base, dash + 1)
      base = substr(base, 1, dash - 1)
      if (prerelease == "" || !valid_identifiers(prerelease, 1)) exit 1
    }
    count = split(base, core, ".")
    if (count != 3) exit 1
    for (part = 1; part <= 3; part += 1) {
      if (core[part] !~ /^[0-9]+$/ ||
          (length(core[part]) > 1 && substr(core[part], 1, 1) == "0")) exit 1
    }
  }
  '
}

acquire_install_lock() {
  install_lock=${data_root}.install.lock
  candidate_lock=$(mktemp "${install_lock}.candidate.XXXXXX") || \
    fail "could not stage installation lock"
  printf '%s\n' "$$" > "$candidate_lock"
  if ln "$candidate_lock" "$install_lock" 2>/dev/null; then
    rm -f "$candidate_lock"
    candidate_lock=
    install_lock_acquired=1
    return
  fi
  rm -f "$candidate_lock"
  candidate_lock=
  if [ -L "$install_lock" ] || [ ! -f "$install_lock" ]; then
    fail "another standalone installation operation is in progress"
  fi
  lock_owner=$(cat "$install_lock")
  case "$lock_owner" in
    ''|*[!0-9]*) fail "another standalone installation operation is in progress" ;;
  esac
  if kill -0 "$lock_owner" 2>/dev/null; then
    fail "another standalone installation operation is in progress"
  fi
  stale_lock=${install_lock}.stale.$$
  mv "$install_lock" "$stale_lock" 2>/dev/null || \
    fail "another standalone installation operation is in progress"
  if [ -L "$stale_lock" ] || [ ! -f "$stale_lock" ]; then
    mv "$stale_lock" "$install_lock" 2>/dev/null || true
    fail "stale installation lock is not a regular file"
  fi
  rm -f "$stale_lock"
  acquire_install_lock
}

select_profile() {
  profile="${HARNESS_SHELL_PROFILE:-}"
  if [ -z "$profile" ]; then
    case "${SHELL:-}" in
      */zsh) profile="${HOME}/.zshrc" ;;
      */bash) profile="${HOME}/.bashrc" ;;
      */fish) profile="${HOME}/.config/fish/conf.d/adaptive-harness.fish" ;;
      *) profile="${HOME}/.profile" ;;
    esac
  fi
  if [ -L "$profile" ]; then
    fail "refusing to modify a symlinked shell profile: $profile"
  fi
  validate_safe_absolute_path "shell profile" "$profile"
  profile_parent=${profile%/*}
  validate_directory_ancestors "shell profile" "${profile_parent:-/}"
  if [ -e "$profile" ] && [ ! -f "$profile" ]; then
    fail "shell profile is not a regular file: $profile"
  fi

  configure_profile_format
}

configure_profile_format() {
  if [ "${profile##*.}" = "fish" ]; then
    escaped=$(printf '%s' "$install_dir" | sed "s/'/'\\\\''/g")
    path_line="fish_add_path '$escaped'"
  else
    escaped=$(printf '%s' "$install_dir" | sed "s/'/'\\\\''/g")
    path_line="export PATH='$escaped':\"\$PATH\""
  fi
  start_marker="# >>> adaptive-harness PATH >>>"
  end_marker="# <<< adaptive-harness PATH <<<"
}

detect_managed_profile() {
  select_profile
  inspect_managed_profile
  if [ "$has_start" = "1" ] && [ "$has_end" = "1" ]; then
    managed_profile=$profile
  elif [ "$has_start" != "$has_end" ]; then
    fail "managed PATH block is malformed in $profile"
  fi
}

inspect_managed_profile() {
  if [ ! -f "$profile" ]; then
    profile_state=absent
  else
    profile_state=$(awk -v start="$start_marker" -v end="$end_marker" \
      -v expected="$path_line" '
    { lines[NR] = $0 }
    $0 == start { starts += 1; start_line = NR }
    $0 == end { ends += 1; end_line = NR }
    END {
      if (starts == 0 && ends == 0) print "absent"
      else if (starts == 1 && ends == 1 && end_line == start_line + 2 &&
               lines[start_line + 1] == expected) print "managed"
      else print "malformed"
    }
    ' "$profile")
  fi
  case "$profile_state" in
    managed) has_start=1; has_end=1 ;;
    absent) has_start=0; has_end=0 ;;
    *) has_start=1; has_end=0 ;;
  esac
}

validate_safe_absolute_path() {
  label=$1
  value=$2
  case "$value" in
    /*) ;;
    *) fail "$label must be an absolute path" ;;
  esac
  case "$value" in
    *//*|*/./*|*/.|*/../*|*/..|*'"'*|*'\'*)
      fail "$label must be a canonical safe path"
      ;;
  esac
  if [ "$(printf '%s' "$value" | wc -l | tr -d ' ')" != "0" ]; then
    fail "$label must not contain a newline"
  fi
}

validate_directory_ancestors() {
  label=$1
  candidate=$2
  while [ "$candidate" != "/" ]; do
    if [ -L "$candidate" ]; then
      fail "$label has a symlinked ancestor: $candidate"
    fi
    if [ -e "$candidate" ] && [ ! -d "$candidate" ]; then
      fail "$label has a non-directory ancestor: $candidate"
    fi
    parent=${candidate%/*}
    candidate=${parent:-/}
  done
}

validate_install_paths() {
  validate_safe_absolute_path "install directory" "$install_dir"
  validate_safe_absolute_path "data root" "$data_root"
  case "$data_root" in
    /|"${HOME}") fail "data root must not be / or HOME" ;;
  esac
  case "$install_dir" in
    /|"${HOME}") fail "install directory must not be / or HOME" ;;
  esac
  validate_directory_ancestors "install directory" "$install_dir"
  validate_directory_ancestors "data root" "$data_root"
}

detect_recorded_managed_profile() {
  [ -L "$manifest" ] || return 0
  recorded_profile=$(sed -n 's/^  "path_profile": "\(.*\)"$/\1/p' "$manifest")
  [ -n "$recorded_profile" ] || return 0
  validate_safe_absolute_path "managed shell profile" "$recorded_profile"
  profile=$recorded_profile
  if [ -L "$profile" ] || [ ! -f "$profile" ]; then
    fail "recorded shell profile is missing or unsafe: $profile"
  fi
  configure_profile_format
  inspect_managed_profile
  if [ "$profile_state" = "malformed" ]; then
    fail "managed PATH block is malformed in $profile"
  fi
  if [ "$profile_state" = "managed" ]; then
    managed_profile=$profile
  fi
}

configure_path() {
  select_profile
  inspect_managed_profile
  if [ "$has_start" = "1" ] && [ "$has_end" = "1" ]; then
    managed_profile=$profile
    printf 'PATH is already managed in %s\n' "$profile"
    return
  fi
  if [ "$has_start" != "$has_end" ]; then
    fail "managed PATH block is malformed in $profile"
  fi

  proposed_profile=${temporary_dir}/profile.proposed
  reviewed_profile=${temporary_dir}/profile.reviewed
  profile_block=${temporary_dir}/profile.block
  profile_existed_at_review=0
  if [ -f "$profile" ]; then
    if [ -s "$profile" ] && \
      [ "$(wc -l < "$profile" | tr -d ' ')" != \
        "$(awk 'END { print NR }' "$profile")" ]; then
      fail "shell profile must end with a newline before managed PATH can be added"
    fi
    profile_existed_at_review=1
    cp -p "$profile" "$reviewed_profile"
    cp -p "$profile" "$proposed_profile"
  else
    : > "$proposed_profile"
    chmod 600 "$proposed_profile"
  fi
  : > "$profile_block"
  if [ -s "$proposed_profile" ]; then
    printf '\n' >> "$profile_block"
  fi
  printf '%s\n%s\n%s\n' "$start_marker" "$path_line" "$end_marker" \
    >> "$profile_block"
  cat "$profile_block" >> "$proposed_profile"

  printf 'Proposed PATH update in %s:\n' "$profile"
  if diff -U 0 /dev/null "$profile_block" >/dev/null; then
    :
  else
    diff_status=$?
    [ "$diff_status" -eq 1 ] || fail "could not review PATH profile diff"
  fi
  printf '%s\n%s\n%s\n' \
    "--- $profile" \
    "+++ $profile" \
    "@@ append managed PATH block; existing content redacted @@"
  sed 's/^/+/' "$profile_block"
  confirmed=0
  if [ "${HARNESS_CONFIRM_PATH:-0}" = "1" ]; then
    confirmed=1
  elif [ "${HARNESS_NONINTERACTIVE:-0}" != "1" ] && [ -r /dev/tty ]; then
    printf 'Apply this PATH update? [y/N] ' >/dev/tty
    answer=
    IFS= read -r answer </dev/tty || true
    case "$answer" in
      y|Y|yes|YES) confirmed=1 ;;
    esac
  fi
  if [ "$confirmed" != "1" ]; then
    printf 'Shell profile was not changed. Run: %s\n' "$path_line"
    return
  fi

  if [ "$profile_existed_at_review" = "1" ]; then
    if [ ! -f "$profile" ] || [ -L "$profile" ] || \
      ! cmp -s "$reviewed_profile" "$profile"; then
      fail "shell profile changed after review: $profile"
    fi
  elif [ -e "$profile" ] || [ -L "$profile" ]; then
    fail "shell profile changed after review: $profile"
  fi

  profile_dir=${profile%/*}
  [ "$profile_dir" = "$profile" ] || mkdir -p "$profile_dir"
  staged_profile=$(mktemp "${profile_dir}/.adaptive-harness-profile.XXXXXX")
  if [ -f "$profile" ]; then
    profile_existed=1
    profile_backup=${temporary_dir}/profile.backup
    cp -p "$profile" "$profile_backup"
    cp -p "$proposed_profile" "$staged_profile"
  else
    cp "$proposed_profile" "$staged_profile"
    chmod 600 "$staged_profile"
  fi
  if [ "$profile_existed_at_review" = "1" ]; then
    if [ ! -f "$profile" ] || [ -L "$profile" ] || \
      ! cmp -s "$reviewed_profile" "$profile"; then
      fail "shell profile changed while preparing update: $profile"
    fi
  elif [ -e "$profile" ] || [ -L "$profile" ]; then
    fail "shell profile changed while preparing update: $profile"
  fi
  profile_changed=1
  mv -f "$staged_profile" "$profile"
  staged_profile=
  managed_profile=$profile
  printf 'Updated PATH in %s; open a new shell before running harness.\n' "$profile"
}

json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

resolve_state_paths() {
  if [ -n "${HARNESS_INSTALL_MANIFEST:-}" ]; then
    manifest=${HARNESS_INSTALL_MANIFEST}
    data_root=${manifest%/*}
  elif [ -n "${HARNESS_STATE_DIR:-}" ]; then
    data_root=${HARNESS_STATE_DIR}
    manifest=${data_root}/installation.json
  elif [ -n "${XDG_DATA_HOME:-}" ]; then
    data_root=${XDG_DATA_HOME}/harness
    manifest=${data_root}/installation.json
  else
    data_root=${HOME}/.local/share/harness
    manifest=${data_root}/installation.json
  fi
  runtime_path=${data_root}/runtime/current
}

write_install_manifest() {
  manifest_target=$1
  if [ -n "$managed_profile" ]; then
    profile_json="\"$(json_escape "$managed_profile")\""
  else
    profile_json=null
  fi
  cat > "$manifest_target" <<EOF
{
  "schema_version": "1.0",
  "channel": "standalone",
  "version": "$(json_escape "$version")",
  "binary_path": "$(json_escape "${install_dir}/harness")",
  "data_root": "$(json_escape "$data_root")",
  "runtime_path": "$(json_escape "$runtime_path")",
  "release_base": "$(json_escape "$release_base")",
  "release_repository": "$(json_escape "$repository")",
  "path_profile": ${profile_json}
}
EOF
  chmod 600 "$manifest_target"
}

replace_pointer() {
  pointer=$1
  target_path=$2
  temporary_pointer=$(mktemp "${runtime_parent}/.pointer.XXXXXX")
  rm -f "$temporary_pointer"
  ln -s "$target_path" "$temporary_pointer"
  if ! mv -f -h "$temporary_pointer" "$pointer" 2>/dev/null; then
    mv -f -T "$temporary_pointer" "$pointer"
  fi
}

pointer_target() {
  pointer=$1
  [ -L "$pointer" ] || fail "runtime pointer is unsafe: $pointer"
  link_target=$(readlink "$pointer")
  case "$link_target" in
    slots/*)
      slot_name=${link_target#slots/}
      case "$slot_name" in
        ""|.|..|*/*) fail "runtime pointer target is unsafe: $pointer" ;;
      esac
      ;;
    *) fail "runtime pointer target is unsafe: $pointer" ;;
  esac
  resolved_target=${runtime_parent}/${link_target}
  [ -d "$resolved_target" ] && [ ! -L "$resolved_target" ] || \
    fail "runtime pointer target is unavailable: $pointer"
  printf '%s\n' "$link_target"
}

report_linux_sandbox() {
  case "$target" in
    linux-*) ;;
    *) return ;;
  esac
  bwrap_command="${HARNESS_BWRAP_COMMAND:-bwrap}"
  if ! command -v "$bwrap_command" >/dev/null 2>&1; then
    printf '%s\n' \
      'Bubblewrap is not installed; base CLI and observe remains available.' \
      'Linux enforced execution requires Bubblewrap and working user namespaces.' \
      'Install it with the system package manager (Debian/Ubuntu: sudo apt install bubblewrap), then run harness doctor.'
    return
  fi
  if ! "$bwrap_command" --unshare-user --ro-bind / / -- /bin/true \
    >/dev/null 2>&1; then
    printf '%s\n' \
      'Bubblewrap is installed but its user-namespace probe failed.' \
      'Base CLI and observe remains available; run harness doctor for details.'
  fi
}

require_command curl
require_command tar
require_command awk
require_command diff
require_command cmp
require_command ln
require_command mktemp

target="${HARNESS_TARGET:-$(detect_target)}"
case "$target" in
  macos-arm64|macos-x86_64|linux-arm64|linux-x86_64) ;;
  *) fail "unsupported release target: $target" ;;
esac

if [ -z "$version" ]; then
  version=$(resolve_version)
fi
case "$version" in
  v*) version=${version#v} ;;
esac
if ! validate_semver "$version"; then
  fail "version must be a semantic version"
fi
if ! printf '%s\n' "$repository" | awk '
  /^[^[:space:]\/]+\/[^[:space:]\/]+$/ { valid = 1 }
  END { exit(valid ? 0 : 1) }
'; then
  fail "release repository must be owner/project"
fi

resolve_state_paths
validate_install_paths

tag="v${version}"
archive_name="adaptive-harness-${tag}-${target}.tar.gz"
release_url="${release_base}/${tag}"
temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/adaptive-harness-install.XXXXXX")
cleanup() {
  exit_status=$?
  set +e
  recovery_incomplete=0
  if [ "$install_committed" != "1" ]; then
    if [ "$current_changed" = "1" ]; then
      current_target=
      [ ! -L "$runtime_path" ] || current_target=$(readlink "$runtime_path")
      if [ "$current_target" = "$new_runtime_target" ]; then
        if [ -n "$old_current_target" ]; then
          replace_pointer "$runtime_path" "$old_current_target" || \
            recovery_incomplete=1
        else
          rm -f "$runtime_path" || recovery_incomplete=1
        fi
      elif [ "$current_target" != "$old_current_target" ]; then
        recovery_incomplete=1
      fi
    fi
    if [ "$previous_changed" = "1" ]; then
      previous_target=
      [ ! -L "$previous_runtime" ] || previous_target=$(readlink "$previous_runtime")
      if [ "$previous_target" = "$old_current_target" ]; then
        if [ -n "$old_previous_target" ]; then
          replace_pointer "$previous_runtime" "$old_previous_target" || \
            recovery_incomplete=1
        else
          rm -f "$previous_runtime" || recovery_incomplete=1
        fi
      elif [ "$previous_target" != "$old_previous_target" ]; then
        recovery_incomplete=1
      fi
    fi
    if [ "$created_manifest_link" = "1" ]; then
      if [ -L "$manifest" ] && \
        [ "$(readlink "$manifest")" = "$manifest_link_target" ]; then
        rm -f "$manifest" || recovery_incomplete=1
      elif [ -e "$manifest" ] || [ -L "$manifest" ]; then
        recovery_incomplete=1
      fi
    fi
    if [ "$launcher_changed" = "1" ]; then
      if [ -f "${install_dir}/harness" ] && [ ! -L "${install_dir}/harness" ] && \
        cmp -s "$launcher_installed" "${install_dir}/harness"; then
        if [ "$launcher_existed" = "1" ]; then
          launcher_restore=$(mktemp "${install_dir}/.harness-restore.XXXXXX")
          cp -p "$launcher_backup" "$launcher_restore"
          mv -f "$launcher_restore" "${install_dir}/harness" || \
            recovery_incomplete=1
        else
          rm -f "${install_dir}/harness" || recovery_incomplete=1
        fi
      elif [ "$launcher_existed" = "1" ] && \
        cmp -s "$launcher_backup" "${install_dir}/harness"; then
        :
      elif [ "$launcher_existed" = "0" ] && \
        [ ! -e "${install_dir}/harness" ] && [ ! -L "${install_dir}/harness" ]; then
        :
      else
        recovery_incomplete=1
      fi
    fi
    if [ "$profile_changed" = "1" ]; then
      if [ -f "$profile" ] && [ ! -L "$profile" ] && \
        cmp -s "$proposed_profile" "$profile"; then
        if [ "$profile_existed" = "1" ]; then
          profile_restore=$(mktemp "${profile%/*}/.adaptive-harness-restore.XXXXXX")
          cp -p "$profile_backup" "$profile_restore"
          mv -f "$profile_restore" "$profile" || recovery_incomplete=1
        else
          rm -f "$profile" || recovery_incomplete=1
        fi
      elif [ "$profile_existed" = "1" ] && \
        cmp -s "$profile_backup" "$profile"; then
        :
      elif [ "$profile_existed" = "0" ] && \
        [ ! -e "$profile" ] && [ ! -L "$profile" ]; then
        :
      else
        recovery_incomplete=1
      fi
    fi
    if [ -n "$new_runtime" ] && [ -d "$new_runtime" ]; then
      if [ -L "$runtime_path" ] && \
        [ "$(readlink "$runtime_path")" = "$new_runtime_target" ]; then
        recovery_incomplete=1
      else
        rm -rf "$new_runtime"
      fi
    fi
    if [ "$recovery_incomplete" = "1" ]; then
      printf 'harness installer: recovery was incomplete; retained active recovery artifacts\n' >&2
    fi
  fi
  rm -rf "$temporary_dir"
  if [ -n "$staged_runtime" ] && [ -d "$staged_runtime" ]; then
    rm -rf "$staged_runtime"
  fi
  if [ -n "$staged_profile" ] && [ -f "$staged_profile" ]; then
    rm -f "$staged_profile"
  fi
  if [ -n "$candidate_lock" ] && [ -f "$candidate_lock" ]; then
    rm -f "$candidate_lock"
  fi
  if [ "$install_lock_acquired" = "1" ] && [ -n "$install_lock" ]; then
    lock_owner=
    [ ! -f "$install_lock" ] || [ -L "$install_lock" ] || \
      lock_owner=$(cat "$install_lock")
    if [ "$lock_owner" = "$$" ]; then
      rm -f "$install_lock"
    fi
  fi
  if [ "$install_committed" != "1" ]; then
    if [ "$runtime_slots_existed" = "0" ] && [ -n "$runtime_slots" ] && \
      [ -d "$runtime_slots" ]; then
      rmdir "$runtime_slots" 2>/dev/null || \
        printf 'harness installer: could not remove new runtime slots directory\n' >&2
    fi
    if [ "$runtime_parent_existed" = "0" ] && [ -n "$runtime_parent" ] && \
      [ -d "$runtime_parent" ]; then
      rmdir "$runtime_parent" 2>/dev/null || \
        printf 'harness installer: could not remove new runtime directory\n' >&2
    fi
    if [ "$data_root_existed" = "0" ] && [ -n "$data_root" ] && \
      [ -d "$data_root" ]; then
      rmdir "$data_root" 2>/dev/null || \
        printf 'harness installer: could not remove new data directory\n' >&2
    fi
  fi
  return "$exit_status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

[ -d "$data_root" ] && data_root_existed=1
mkdir -p "$data_root"
acquire_install_lock

curl -fsSL "${release_url}/${archive_name}" -o "${temporary_dir}/${archive_name}"
curl -fsSL "${release_url}/SHA256SUMS" -o "${temporary_dir}/SHA256SUMS"
expected=$(awk -v name="$archive_name" '$2 == name { print $1 }' \
  "${temporary_dir}/SHA256SUMS")
[ -n "$expected" ] || fail "release checksum is missing for ${archive_name}"
actual=$(sha256_file "${temporary_dir}/${archive_name}")
[ "$actual" = "$expected" ] || fail "release checksum verification failed"

tar -xzf "${temporary_dir}/${archive_name}" -C "$temporary_dir" runtime
[ -f "${temporary_dir}/runtime/harness" ] || fail "release archive has no harness runtime"
[ ! -L "${temporary_dir}/runtime/harness" ] || fail "release runtime executable is unsafe"
chmod 755 "${temporary_dir}/runtime/harness"

runtime_parent=${data_root}/runtime
previous_runtime=${runtime_parent}/previous
runtime_slots=${runtime_parent}/slots
[ -d "$runtime_parent" ] && runtime_parent_existed=1
[ -d "$runtime_slots" ] && runtime_slots_existed=1
mkdir -p "$runtime_slots"
staged_runtime=$(mktemp -d "${runtime_slots}/.${version}.XXXXXX")
cp -R "${temporary_dir}/runtime/." "$staged_runtime"
runtime_version=$("${staged_runtime}/harness" --version) || \
  fail "release runtime failed its version check"
[ "$runtime_version" = "$version" ] || fail "release runtime version does not match"

manifest_link_target=runtime/current/installation.json
if [ -L "$manifest" ]; then
  [ "$(readlink "$manifest")" = "$manifest_link_target" ] || \
    fail "installation manifest pointer is unsafe"
elif [ -e "$manifest" ]; then
  fail "installation manifest path is unsafe"
fi
if [ -L "$previous_runtime" ]; then
  old_previous_target=$(pointer_target "$previous_runtime")
elif [ -e "$previous_runtime" ]; then
  fail "previous runtime pointer is unsafe"
fi
if [ -L "$runtime_path" ]; then
  old_current_target=$(pointer_target "$runtime_path")
elif [ -e "$runtime_path" ]; then
  fail "current runtime pointer is unsafe"
fi

mkdir -p "$install_dir"
if [ -e "${install_dir}/harness" ] || [ -L "${install_dir}/harness" ]; then
  [ -f "${install_dir}/harness" ] && [ ! -L "${install_dir}/harness" ] || \
    fail "installed launcher path is unsafe"
  launcher_existed=1
  launcher_backup=${temporary_dir}/launcher.backup
  cp -p "${install_dir}/harness" "$launcher_backup"
fi
staged=$(mktemp "${install_dir}/.harness.XXXXXX")
escaped_runtime=$(printf '%s' "${runtime_path}/harness" | \
  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/\\$/g' -e 's/`/\\`/g')
cat > "$staged" <<EOF
#!/bin/sh
exec "$escaped_runtime" "\$@"
EOF
chmod 755 "$staged"
launcher_installed=${temporary_dir}/launcher.installed
cp -p "$staged" "$launcher_installed"
launcher_changed=1
mv -f "$staged" "${install_dir}/harness"

case ":${PATH}:" in
  *":${install_dir}:"*)
    detect_recorded_managed_profile
    [ -n "$managed_profile" ] || detect_managed_profile
    ;;
  *)
    printf '%s is not on PATH. Add it before running harness in a new shell.\n' \
      "$install_dir"
    configure_path
    ;;
esac

write_install_manifest "${staged_runtime}/installation.json"
mkdir -p "$data_root"
if [ -L "$manifest" ]; then
  :
else
  temporary_manifest_link=$(mktemp "${data_root}/.installation.XXXXXX")
  rm -f "$temporary_manifest_link"
  ln -s "$manifest_link_target" "$temporary_manifest_link"
  created_manifest_link=1
  mv -f "$temporary_manifest_link" "$manifest"
fi

if [ -n "$old_current_target" ]; then
  previous_changed=1
  replace_pointer "$previous_runtime" "$old_current_target"
fi
new_runtime=$staged_runtime
new_runtime_target="slots/${staged_runtime##*/}"
current_changed=1
replace_pointer "$runtime_path" "$new_runtime_target"
staged_runtime=

installed_version=$("${install_dir}/harness" --version) || installed_version=
if [ "$installed_version" != "$version" ]; then
  fail "installed runtime failed its version check"
fi
install_committed=1
if [ -n "$old_previous_target" ] && \
  [ "$old_previous_target" != "$old_current_target" ]; then
  if ! rm -rf "${runtime_parent}/${old_previous_target}"; then
    printf 'harness installer: retained stale previous runtime for manual cleanup\n' >&2
  fi
fi
printf 'Installed Adaptive Harness %s at %s/harness\n' "$version" "$install_dir"
report_linux_sandbox
