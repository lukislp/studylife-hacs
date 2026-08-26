# [1.4.0](https://github.com/lukislp/studylife-hacs/compare/v1.3.3...v1.4.0) (2026-08-26)


### Features

* read all metrics from the StudyLife server instead of computing them ([860b9be](https://github.com/lukislp/studylife-hacs/commit/860b9be7d865a668e46375b9d5faa5ac744a979a))

## [1.3.3](https://github.com/lukislp/studylife-hacs/compare/v1.3.2...v1.3.3) (2026-08-26)


### Bug Fixes

* bound week hours to the week and fetch real elective ECTS quotas ([b80a7dd](https://github.com/lukislp/studylife-hacs/commit/b80a7dd3a1db14dd98444e5cdbbd3ec4644cad28))

## [1.3.2](https://github.com/lukislp/studylife-hacs/compare/v1.3.1...v1.3.2) (2026-08-05)


### Bug Fixes

* apply the full monthly goal instead of the elapsed-weeks proration ([599f295](https://github.com/lukislp/studylife-hacs/commit/599f29519ad1e6fba393a2ad2ad26c0da205a23a))

## [1.3.1](https://github.com/lukislp/studylife-hacs/compare/v1.3.0...v1.3.1) (2026-08-05)


### Bug Fixes

* trigger a release for the 100% coverage test additions ([ed617f5](https://github.com/lukislp/studylife-hacs/commit/ed617f5479b23b2bcbdd038c5f11b5b34cec48c0))

# [1.3.0](https://github.com/lukislp/studylife-hacs/compare/v1.2.1...v1.3.0) (2026-08-05)


### Bug Fixes

* mark generate_coverage_badge.py executable ([0a5f950](https://github.com/lukislp/studylife-hacs/commit/0a5f950e3c28220a45c4c95cc6f461fad3da3be8))


### Features

* add a self-hosted test coverage badge ([754bc3c](https://github.com/lukislp/studylife-hacs/commit/754bc3cc94ac3d9ed4f4aa0a6088aaa805f12bb5))

## [1.2.1](https://github.com/lukislp/studylife-hacs/compare/v1.2.0...v1.2.1) (2026-08-05)


### Bug Fixes

* lead the README with a concise feature summary before registry details ([092a7da](https://github.com/lukislp/studylife-hacs/commit/092a7da613125bc61d75931c9ba3a76bf954cc9f))

# [1.2.0](https://github.com/lukislp/studylife-hacs/compare/v1.1.3...v1.2.0) (2026-08-05)


### Bug Fixes

* stop skipping the hacs brands check now that a local icon exists ([50c7283](https://github.com/lukislp/studylife-hacs/commit/50c7283d6769873ebdbc4c0c6e0d2fafe0cf66aa))


### Features

* add local brand icon via Home Assistant's brand proxy API ([aa115c6](https://github.com/lukislp/studylife-hacs/commit/aa115c61081a743979fe299c824efc80a3c97c95))

## [1.1.3](https://github.com/lukislp/studylife-hacs/compare/v1.1.2...v1.1.3) (2026-08-05)


### Bug Fixes

* correct codeowners to match the actual GitHub account ([@lukislp](https://github.com/lukislp)) ([a86a399](https://github.com/lukislp/studylife-hacs/commit/a86a399b4ed4cfb0a18302955995cd6c61995f21))

## [1.1.2](https://github.com/lukislp/studylife-hacs/compare/v1.1.1...v1.1.2) (2026-08-05)


### Bug Fixes

* surface build/release/license status via README badges ([3eae438](https://github.com/lukislp/studylife-hacs/commit/3eae438c83a6e47b88503ea1d64805db68eb8050))

## [1.1.1](https://github.com/lukislp/studylife-hacs/compare/v1.1.0...v1.1.1) (2026-08-05)


### Bug Fixes

* don't override explicit zero quota goals with built-in defaults ([49dd655](https://github.com/lukislp/studylife-hacs/commit/49dd655baf43b081f99d0cd21619f212ce8fa6fa))

# [1.1.0](https://github.com/lukislp/studylife-hacs/compare/v1.0.0...v1.1.0) (2026-08-04)


### Bug Fixes

* add required manifest issue_tracker, defer brands validation ([d53debc](https://github.com/lukislp/studylife-hacs/commit/d53debc6e0daf01f421243c2f7a9395f4b16a2de))
* mark sync_manifest_version.py executable ([44068cb](https://github.com/lukislp/studylife-hacs/commit/44068cbd89b9f24e583aedc9b9d5129a0b25c5ed))
* sort manifest.json keys per hassfest's required order ([642f734](https://github.com/lukislp/studylife-hacs/commit/642f73442c73015777ff2cd27e1de3b8ecb97ce5))


### Features

* add GitHub Actions CI/CD pipeline ([fb14861](https://github.com/lukislp/studylife-hacs/commit/fb148610536b1475c7150ba5355cdaf50eb8f204))
