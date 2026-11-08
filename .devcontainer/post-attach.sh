#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Prints the next steps each time an editor attaches to the dev container.
set -euo pipefail

if [ -n "${CODESPACE_NAME:-}" ]; then
  url="https://${CODESPACE_NAME}-8123.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
else
  url="http://localhost:8123"
fi

cat <<MSG

StudyLife dev container

  Run the tests (as CI does):
    pytest tests/ -v --cov=custom_components/studylife --cov-report=json:coverage.json

  Lint (as CI does):
    ruff check . && ruff format --check .

  Start Home Assistant with this integration loaded from the checkout:
    scripts/develop
  then open ${url}
  The first start takes a few minutes (Home Assistant installs its requirements into
  config/deps). Onboarding creates a local owner account in config/.

MSG
