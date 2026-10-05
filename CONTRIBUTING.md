# Contributing & release process

## Branches and commits

* `main` is always releasable. Work happens on short-lived branches (`feat/…`, `fix/…`) merged by pull request once CI is green.
* Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(integration): …`, `fix(firmware): …`, `docs: …`, `test: …`, `ci: …`, `chore: …`.
  Scopes: `integration` (custom_components), `firmware` (esphome/), `docs`, `ci`.
  A breaking change gets a `!` (`feat(firmware)!: …`) and a `BREAKING CHANGE:` footer.

## Versioning

The project uses [Semantic Versioning](https://semver.org/). One version covers the whole repository: the HA
integration and the ESPHome gateway firmware ship together because they share the
[webhook protocol](docs/webhook-protocol.md).

| Change | Bump |
|---|---|
| Incompatible webhook protocol change, removed/renamed entities or options, config entry migration that cannot be undone | **major** |
| New feature, new entity, new option, new protocol field (backwards compatible) | **minor** |
| Bug fix, docs, internal refactor | **patch** |

While the version is `0.y.z`, minor bumps may still contain breaking changes. Those are called out in the changelog.

The webhook protocol has its own `"v"` field. It only increases on an incompatible protocol change, and the integration
keeps accepting the previous version for at least one minor release.

## Making a release

1. Move the entries under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) to a new `## [X.Y.Z] - YYYY-MM-DD` section
   and update the compare links at the bottom.
2. Set `"version": "X.Y.Z"` in `custom_components/tempem_ble/manifest.json`.
3. Commit both as `chore(release): vX.Y.Z` (normally in a release PR) and merge to `main`.

The **Release** workflow sees a `manifest.json` version on `main` that has no tag yet. It checks that `CHANGELOG.md` has
a section for it, creates the `vX.Y.Z` tag and a GitHub release whose notes are that section, and attaches
`tempem_ble.zip` (the file HACS installs, see `hacs.json`). HACS then offers the new version to users. Pushing a
`vX.Y.Z` tag by hand also works, and the tag must then match `manifest.json`. Re-running the workflow for a version that
is already tagged does nothing.

### Publishing releases under your own account

By default the workflow publishes with the built-in `GITHUB_TOKEN`, so the tag and release show `github-actions` as
author. To publish them as the maintainer, create a fine-grained personal access token for this repository with
**Contents: Read and write** and save it as the Actions secret `RELEASE_TOKEN`. The workflow uses it when present.

## Local checks

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check custom_components tests && ruff format --check custom_components tests
pytest
# firmware
pip install -r esphome/requirements.txt
esphome config esphome/tempem-remote-gateway.yaml
esphome compile esphome/tempem-remote-gateway.yaml   # full build, as CI does
```
