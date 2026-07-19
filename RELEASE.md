# Releasing the standalone Wayfinder router

This guide governs standalone `wayfinder-router` PyPI releases. That product uses CalVer and bare
numeric tags. Wayfinder Desktop uses SemVer, `desktop-v*` tags, and the separate procedure in
`macos/WayfinderMac/Packaging/RELEASE.md`.

The router process is deliberately small and mechanical: its version is single-sourced, its
changelog section supplies the release notes, and pushing a matching tag publishes through the
release workflow.

## Versioning

The standalone router uses **CalVer**, `YYYY.MM.MICRO`:

- `YYYY` — the year (e.g. `2026`).
- `MM` — the calendar month of the release (`6` = June, `7` = July). Not zero-padded.
- `MICRO` — a counter within that month, incrementing per release and starting at `0` for the first
  release of a new month.

Examples: `2026.6.10` is the tenth release in June 2026; `2026.7.0` is the first in July.

The number says **when**, not **how big**. A router release's theme is carried by the one-line
summary under its changelog header, never by the version.

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

Standalone-router changes land under `## Standalone router — Unreleased` in `CHANGELOG.md`.
Desktop entries remain in their independent Desktop section and are never consumed by a router
release. Keep entries about **user impact**, not implementation, and link the ADR for detail.

## Cutting a release

Start from a clean, up-to-date `main`, then create a `codex/release-*` branch. Never commit the
release cut directly to the protected branch.

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

4. **Roll only the standalone-router changelog** in `CHANGELOG.md`: rename
   `## Standalone router — Unreleased` to `## v<new> — <YYYY-MM-DD>` (today's date), then add a
   fresh empty `## Standalone router — Unreleased` above it. Leave Desktop release notes untouched.

5. **Commit both files together** with a `chore(release)` subject, a `[release:<new>]` trailer, and a
   body (every commit carries a descriptive body):

   ```sh
   git commit -F - <<'MSG'
   chore(release): roll the v<new> changelog and bump __version__ [release:<new>]

   <one short paragraph: what the release contains and that it is a mechanical
   cut — version bump + changelog roll, no behaviour change.>
   MSG
   ```

6. **Push the release branch and open a pull request.** Merge only after required checks and review
   pass.

7. **Sync local `main`, then tag the reviewed merge commit** (annotated, bare, matching
   `__version__` exactly) and push the tag:

   ```sh
   git switch main
   git pull --ff-only
   git tag -a <new> -m "v<new>"      # e.g. 2026.7.0
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
