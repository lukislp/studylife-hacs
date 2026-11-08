# Contributing to the StudyLife Home Assistant integration

Thanks for taking the time. This is a single-maintainer project, so the process is deliberately
small - but it is the same for every change, including the maintainer's own.

## How changes get in

1. Open an issue first for anything bigger than a typo or an obvious bug fix, so the direction can
   be agreed before you spend time on it. Use the templates under `.github/ISSUE_TEMPLATE/`.
2. Fork the repository (or branch, if you have write access) and make your change on a branch.
3. Open a pull request against `main`. The pull-request template asks for what changed and why.
4. `main` is protected: a PR merges only after the test stage is green and the branch is up to
   date with `main` (enable auto-merge and it lands on its own once that is the case). Nobody
   pushes to `main` directly, not even the maintainer.

## What a pull request needs

- **Conventional Commits.** The version and the release notes are generated from the commit messages
  (`feat:` = minor release, `fix:` = patch release, `build:`/`ci:`/`docs:`/`test:` = no release).
  Squash-merge keeps the PR title as the commit message, so give the PR a
  Conventional Commit title.
- **Green required checks.** `ci / test-unit`, `ci / test-lint`, `ci / test-hassfest`,
  `ci / test-hacs-validation` and `review / dependency-review` are required; a red one blocks the
  merge.
- **Tests for new functionality.** New features and bug fixes come with tests in `tests/`. A PR
  that adds behaviour without a test is asked to add one. The coverage reported by the
  `ci / test-unit` job is expected not to drop.
- **Lint and formatting.** `test-lint` runs `ruff check .` **and** `ruff format --check .`. Run
  both before pushing; `ruff check` passing does not mean the formatting is clean.
- **Home Assistant's own gates.** `test-hassfest` validates the manifest and integration structure
  the way Home Assistant core does, and `test-hacs-validation` runs the HACS integration check. A
  new dependency, iot_class or config-flow change usually shows up here first.
- **Hash-pinned requirements.** The requirement files are compiled with hashes and installed with
  `--require-hashes`, so adding or bumping a dependency means editing the matching `.in` file and
  regenerating the `.txt` (see below) - not hand-editing the `.txt`.

## Releases

`.github/workflows/ci-cd.yml` looks at every push to `main` through the release module of the
lktec-bot GitHub App (job `release`; policy and rules in `.github/lktec-bot.yml`), after the tests
of the `ci` job passed. The version comes from the Conventional Commits since the highest `v*`
tag: breaking change -> major, `feat` -> minor, `fix`/`perf`/`revert` -> patch; docs, ci, build,
chore, test and refactor commits publish nothing. When a release is due, the App creates the tag
`v<version>` together with the GitHub release (and its notes) through the API, then the `publish`
job builds `studylife.zip` from that commit with the version written into `manifest.json` (the
repository's manifest stays at `0.0.0`; HACS installs the zip, `zip_release` in `hacs.json`) and
attaches it with `SHA256SUMS`, Sigstore bundles and a build provenance attestation. A release makes
no commit.

The tag itself carries no signature: GitHub does not sign tag objects that an App creates.
`studylife.zip`, `SHA256SUMS`, the Sigstore bundles and the build provenance are what to verify.

**Secrets of the release:** `LKTEC_BOT_APP_ID` and `LKTEC_BOT_PRIVATE_KEY` (the App must be
installed on the repository).

**A failed release.** When `publish` fails, the tag and the GitHub release already exist; re-run
the failed job (`gh run rerun <run-id> --failed`). Do not delete or move a published tag.

## CI in this repository

[`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml) is a thin caller; the actual jobs live
in the shared [`lukislp/ci-workflows`](https://github.com/lukislp/ci-workflows) reusable workflow
`hacs-ci.yml`, called with `component: studylife` and `fuzz-script: fuzz/fuzz_coordinator.py`. That
is why the check names are prefixed `ci / `. CI runs on Python 3.14.

## Running things locally

```bash
pip install --require-hashes -r requirements_test.txt
pytest tests/ -v --cov=custom_components/studylife --cov-report=json:coverage.json
```

```bash
pip install --require-hashes -r requirements_lint.txt
ruff check .
ruff format --check .
```

The fuzz job is Linux-only (`atheris` publishes no Windows wheel) and is not a required check:

```bash
pip install --require-hashes -r requirements_test.txt -r requirements_fuzz.txt
PYTHONPATH=. python fuzz/fuzz_coordinator.py -max_total_time=30 -rss_limit_mb=1024
```

To try the integration against a real Home Assistant, copy `custom_components/studylife` into your
instance's `custom_components/` directory and restart.

### Codespaces / Dev Container

`.devcontainer/` gives you a ready environment in GitHub Codespaces ("Open in Codespaces" badge in
the README) or in VS Code with the Dev Containers extension: Python 3.14 with the pinned test and
lint requirements installed (the same `--require-hashes` install as CI), the GitHub CLI, and the
system libraries Home Assistant's camera, stream and dhcp components need.

Run the tests right away:

```bash
pytest tests/ -v --cov=custom_components/studylife --cov-report=json:coverage.json
```

To try the integration in a real Home Assistant, run `scripts/develop`. It starts a dev instance
in the foreground on port 8123 (forwarded, private) with `custom_components/studylife` loaded
from the checkout; its configuration lives in the git-ignored `config/`. The first start takes a
few minutes while Home Assistant installs its requirements, and onboarding creates a local owner
account. Stop it with Ctrl+C.

### Regenerating a requirements lock

Edit the `.in` file, then recompile with `pip-tools` under Python 3.14 on Linux (the lock is
resolved for the Linux CI runners; `--upgrade` stops the old pins from constraining the resolver):

```bash
pip-compile --upgrade --allow-unsafe --generate-hashes --no-emit-index-url --strip-extras requirements_test.in
```

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md). The [Code of Conduct](CODE_OF_CONDUCT.md) applies to every
interaction in this repository.
