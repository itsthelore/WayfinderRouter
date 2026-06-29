# Releasing Wayfinder

How a Wayfinder release is cut. The process is deliberately small and mechanical: the version is
single-sourced, the changelog *is* the release notes, and **pushing a tag does the publish**. Follow
the checklist in order and the automation cannot publish a mismatched build — it verifies the tag
against the package version and refuses on a mismatch.

## Versioning

Wayfinder uses **CalVer**, `YYYY.MM.MICRO`:

- `YYYY` — the year (e.g. `2026`).
- `MM` — the calendar month of the release (`6` = June, `7` = July). Not zero-padded.
- `MICRO` — a counter within that month, incrementing per release and starting at `0` for the first
  release of a new month.

Examples: `2026.6.10` is the tenth release in June 2026; `2026.7.0` is the first in July.

The number says **when**, not **how big**. A release's *theme* — "the feedback release", "the macOS
release" — is carried by the one-line summary under its changelog header, never by the version. So a
headline feature release and a one-line fix can share a month; the changelog says which is which.

Tags are **bare** CalVer (`2026.7.0`); the changelog headers carry a cosmetic `v` (`## v2026.7.0`).
The publish workflow accepts either form (`2026.7.0` or `v2026.7.0`) — we tag bare to match
`__version__` exactly.

## One source of version truth

The version lives in exactly one place:

```python
# wayfinder_router/__init__.py
__version__ = "2026.6.9"
```

`pyproject.toml` reads it dynamically (`[tool.setuptools.dynamic] version = { attr =
"wayfinder_router.__version__" }`), so there is never a second copy to forget. The release workflow
**refuses to publish** unless the pushed tag equals `wayfinder_router.__version__`. Bumping this
constant is therefore step one, and the tag must match it byte-for-byte.

## What goes into a release

Everything user-visible lands under `## Unreleased` in `CHANGELOG.md` as it merges — a theme line plus
the usual `### Added` / `### Changed` / `### Fixed` groupings. Cutting a release is mostly *closing*
that section. If `## Unreleased` is empty, there is nothing to release. Keep entries about **user
impact**, not implementation — link the ADR for the detail.

## Cutting a release

From a clean, up-to-date `main`:

1. **Green gate.** Install the dev/optional deps and run the gate locally (mirrors CI):

   ```sh
   pip install -e ".[dev,gateway,ui]" ruff mypy
   ruff check . && mypy wayfinder_router && pytest -q
   ```

   CI additionally asserts the standalone invariant — `import rac` must fail (WF-ADR-0001) — and builds
   the gateway Docker image. Those three commands passing locally is the bar before you tag.

2. **Pick the version** per the scheme above: the next `MICRO` in the current month, or `.0` if this is
   the first release of a new month.

3. **Bump the constant** — edit `wayfinder_router/__init__.py`:

   ```python
   __version__ = "<new>"
   ```

4. **Roll the changelog** in `CHANGELOG.md`: rename `## Unreleased` to `## v<new> — <YYYY-MM-DD>`
   (today's date), keeping its theme line and entries, then add a fresh empty `## Unreleased` above it.

5. **Commit both files together**, matching the existing history's style:

   ```sh
   git commit -am "release: cut v<new> — <one-line theme>"
   ```

6. **Tag the release commit** (annotated, bare, matching `__version__` exactly) and push:

   ```sh
   git tag -a <new> -m "v<new>"      # e.g. 2026.7.0
   git push origin main
   git push origin <new>
   ```

That tag push is the release. Everything after is automation.

## What the tag triggers

`.github/workflows/release.yml` runs on any `v*` or bare-numeric tag:

1. **Verifies** the tag equals `wayfinder_router.__version__` — fails loudly, no publish, on mismatch.
2. **Builds** the sdist + wheel and runs `twine check`.
3. **Publishes** to PyPI via **Trusted Publishing** (OIDC, the `pypi` environment). No API token or
   password is stored — PyPI verifies the workflow's identity.

## Verifying the publish

- Watch the **Release** workflow go green in Actions.
- Confirm the version on PyPI: <https://pypi.org/project/wayfinder-router/>.
- Smoke-test the published artifact in a clean environment:

  ```sh
  pip install "wayfinder-router==<new>"
  python -c "import wayfinder_router; print(wayfinder_router.__version__)"
  ```

## If a release is bad

CalVer numbers are never reused — do not re-tag a published version. Instead:

- **Yank** the broken release on PyPI (hides it from new installs without breaking existing pins), and
- cut a **new** `MICRO` with the fix (e.g. `2026.7.0` → `2026.7.1`) following the checklist above.
