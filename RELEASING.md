# Releasing to PyPI

The package publishes itself from a version tag. There is no API token in this
repository and none in the Actions secrets — PyPI verifies the workflow
identity over OIDC (Trusted Publishing).

```
git tag v7.5.0  →  verify (4 Pythons)  →  build  →  install & probe  →  publish
```

---

## One-time setup

### 1. Claim the name on PyPI

`pyfuse` was unclaimed at the time of writing. Confirm before you tag:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://pypi.org/pypi/pyfuse/json
# 404 = available, 200 = taken
```

### 2. Register the trusted publisher

On PyPI: **Your projects → Publishing → Add a new pending publisher**

| Field | Value |
|---|---|
| PyPI project name | `pyfuse` |
| Owner | `ayanbag` |
| Repository name | `pyfuse` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

A *pending* publisher is the right choice for a project that has never been
uploaded — it creates the project on first publish. Nothing to paste, nothing
to rotate, and a leaked secret is not possible because there isn't one.

### 3. Create the GitHub environment

Repo **Settings → Environments → New environment → `pypi`**. Optionally add
yourself as a required reviewer, so publishing needs a human click even though
tagging is automatic.

---

## Cutting a release

```bash
just check && just test          # green locally first
just dist                        # build + twine check + inspect
git tag v7.5.0 && git push --tags
```

The workflow then refuses to continue unless every one of these holds:

1. ruff, `mypy --strict` and the full pytest suite pass on **3.10, 3.11, 3.12
   and 3.13**.
2. The tag matches `version` in `pyproject.toml`. A `v7.5.1` tag against a
   `7.5.0` pyproject fails the build rather than publishing a mislabelled
   artifact.
3. `twine check` passes on both the sdist and the wheel.
4. The built wheel installs into a clean venv, imports **from outside the
   source tree**, returns a correct search result, and still contains
   `py.typed`.

Only then does it upload.

## About the version number

The package version tracks the fuse.js release it ports: **7.5.0** is fuse.js
v7.5.0. That makes "which upstream does this match?" answerable at a glance,
which matters more for a port than semver purity does.

The cost is real and worth stating: if this port needs a fix while upstream
stays at 7.5.0, the next version has to be `7.5.0.post1`, because `7.5.1`
would later collide with a genuine fuse.js 7.5.1. Post-releases are the
correct tool for exactly this — packaging changes with no upstream change.

## What ships

The wheel contains `src/pyfuse` and nothing else. The sdist adds the port's
own tests, the examples, and the licence files.

`tests/original/` — the vendored fuse.js checkout kept for provenance — is
deliberately **excluded**. It is a 5 MB JavaScript repository that belongs in
version control, not in the download of anyone running `pip install`.

## Verifying a published release

```bash
python -m venv /tmp/check && /tmp/check/bin/pip install pyfuse
cd /tmp && /tmp/check/bin/python -c "
from pyfuse import Fuse
print(Fuse([{'title': 'Old Man\'s War'}], {'keys': ['title']}).search('old man'))"
```

`cd /tmp` is not incidental — running from the repository root would import
`src/pyfuse` and tell you nothing about what PyPI actually served.

## If something goes wrong

PyPI does not allow re-uploading a version, even after deletion. A broken
release is fixed by publishing a new one:

```bash
# bump version in pyproject.toml and src/pyfuse/__init__.py, then
git tag v7.5.0.post1 && git push --tags
```

Yanking (`pip` will skip it unless pinned) is done from the PyPI web UI and is
the right move for a release that installs but misbehaves.
