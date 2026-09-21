#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Generated/maintained for meson run_target; invoked as:
#   bash scripts/<name>.sh <SOURCE_ROOT> <BUILD_ROOT>
set -euo pipefail
SOURCE_ROOT="${1:-${MESON_SOURCE_ROOT:-.}}"
BUILD_ROOT="${2:-${MESON_BUILD_ROOT:-.}}"
prefix="@0@"
bindir="@1@"
datadir="@2@"
mandir="@3@"
for p in "$bindir/netpoisson" "$mandir/man1/netpoisson.1" "$datadir/bash-completion/completions/netpoisson"
do
    if [ -L "$p" ]; then
        sudo rm -f "$p"
        printf "Removed %s\n" "$p"
    else
        printf "Skipped (not a symlink): %s\n" "$p"
    fi
done
