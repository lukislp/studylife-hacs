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

- **Conventional Commits.** The version and the changelog are generated from the commit messages
  (`feat:` = minor release, `fix:` = patch release, `build:`/`ci:`/`docs:`/`test:` = no release).
  The release also syncs the version into `custom_components/studylife/manifest.json`, which is
  what HACS shows users. Squash-merge keeps the PR title as the commit message, so give the PR a
  Conventional Commit title.
- **Green required checks.** `ci / test-unit`, `ci / test-lint`, `ci / test-hassfest`,
  `ci / test-hacs-validation` and `review / dependency-review` are required; a red one blocks the
  merge.
- **Tests for new functionality.** New features and bug fixes come with tests in `tests/`. A PR
  that adds behaviour without a test is asked to add one. The coverage badge in the README is
  regenerated from the coverage report on every release and is expected not to drop.
- **Lint and formatting.** `test-lint` runs `ruff check .` **and** `ruff format --check .`. Run
  both before pushing; `ruff check` passing does not mean the formatting is clean.
- **Home Assistant's own gates.** `test-hassfest` validates the manifest and integration structure
  the way Home Assistant core does, and `test-hacs-validation` runs the HACS integration check. A
  new dependency, iot_class or config-flow change usually shows up here first.
- **Hash-pinned requirements.** The requirement files are compiled with hashes and installed with
  `--require-hashes`, so adding or bumping a dependency means editing the matching `.in` file and
  regenerating the `.txt` (see below) - not hand-editing the `.txt`.

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

### Regenerating a requirements lock

Edit the `.in` file, then recompile with `pip-tools` under Python 3.14:

```bash
pip-compile --allow-unsafe --generate-hashes --no-emit-index-url --no-index --strip-extras requirements_test.in
```

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md). The [Code of Conduct](CODE_OF_CONDUCT.md) applies to every
interaction in this repository.
