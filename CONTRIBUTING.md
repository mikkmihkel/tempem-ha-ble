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
3. Commit: `chore(release): vX.Y.Z`.
4. Tag and push: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin main vX.Y.Z`.

The **Release** workflow then checks that the tag, `manifest.json` and `CHANGELOG.md` agree. It builds
`tempem_ble.zip` (the file HACS installs, see `hacs.json`) and publishes a GitHub release with the changelog section
as its notes. HACS offers the new version to users automatically.

## Local checks

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check custom_components tests && ruff format --check custom_components tests
pytest
# firmware
pip install esphome
cd esphome && cp secrets.yaml.example secrets.yaml && esphome config tempem-remote-gateway.yaml
```
