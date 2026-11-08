#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Installs the pinned test and lint requirements the way CI does (--require-hashes). Codespaces
# runs this in the prebuild and again when a codespace is opened from an older prebuild, so
# the toolchain matches the checked-out requirements files.
set -euo pipefail

cd "$(dirname "$0")/.."

pip install --require-hashes -r requirements_test.txt -r requirements_lint.txt
