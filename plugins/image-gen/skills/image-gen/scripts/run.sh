#!/bin/sh
# Copyright (C) 2026 luckygopher
# SPDX-License-Identifier: GPL-3.0-only
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
skill_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
runtime_version=$(cat "$script_dir/runtime-version")
case "$runtime_version" in ''|*[!0-9A-Za-z.+-]*) echo 'Invalid runtime version.' >&2; exit 1;; esac
case "$(uname -s)" in Darwin) runtime_os=darwin;; Linux) runtime_os=linux;; *) echo 'Use run.ps1 on Windows. Supported systems: macOS, Linux, Windows.' >&2; exit 1;; esac
case "$(uname -m)" in arm64|aarch64) runtime_arch=arm64;; x86_64|amd64) runtime_arch=amd64;; *) echo 'Unsupported CPU. Supported CPUs: arm64 and amd64.' >&2; exit 1;; esac
runtime_name="image-gen-$runtime_os-$runtime_arch"
runtime_binary="$skill_root/bin/$runtime_name"
runtime_checksums="$skill_root/bin/SHA256SUMS"
runtime_base="https://raw.githubusercontent.com/luckygopher/agent-toolkit/image-gen-v$runtime_version/plugins/image-gen/skills/image-gen/bin"

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --location --proto '=https' --connect-timeout 15 --max-time 120 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --timeout=30 -q -O "$2" "$1"
  else
    echo 'This source-only skill needs curl or wget once. Install the packaged skill or plugin, which already includes its runtime.' >&2
    return 1
  fi
}

if [ ! -f "$runtime_binary" ] || [ ! -f "$runtime_checksums" ]; then
  mkdir -p "$skill_root/bin"
  runtime_tmp=$(mktemp -d "$skill_root/bin/.download-XXXXXX")
  trap 'rm -rf "$runtime_tmp"' EXIT HUP INT TERM
  echo 'Preparing the packaged Image Gen runtime. No Python, Node or Go installation is needed.' >&2
  fetch "$runtime_base/SHA256SUMS" "$runtime_tmp/SHA256SUMS"
  fetch "$runtime_base/$runtime_name" "$runtime_tmp/$runtime_name"
  runtime_binary="$runtime_tmp/$runtime_name"
  runtime_checksums="$runtime_tmp/SHA256SUMS"
fi

runtime_expected=$(awk -v name="$runtime_name" '$2 == name { print $1; exit }' "$runtime_checksums")
if [ "${#runtime_expected}" -ne 64 ]; then echo 'Runtime checksum is missing. Reinstall the plugin or skill.' >&2; exit 1; fi
if command -v sha256sum >/dev/null 2>&1; then runtime_actual=$(sha256sum "$runtime_binary" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then runtime_actual=$(shasum -a 256 "$runtime_binary" | awk '{print $1}')
else echo 'A system SHA-256 utility is required to verify the runtime.' >&2; exit 1
fi
if [ "$runtime_actual" != "$runtime_expected" ]; then echo 'Runtime verification failed. No runtime was executed; reinstall the plugin or skill.' >&2; exit 1; fi

if [ "${runtime_tmp:-}" ]; then
  chmod 755 "$runtime_binary"
  mv "$runtime_binary" "$skill_root/bin/$runtime_name"
  mv "$runtime_checksums" "$skill_root/bin/SHA256SUMS"
  rmdir "$runtime_tmp"
  trap - EXIT HUP INT TERM
  runtime_binary="$skill_root/bin/$runtime_name"
fi
if [ ! -x "$runtime_binary" ]; then chmod u+x "$runtime_binary"; fi
exec "$runtime_binary" "$@"
