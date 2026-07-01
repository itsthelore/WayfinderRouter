# Contributing to Wayfinder

Thanks for helping out. Wayfinder is a small, deterministic tool, and a few conventions keep it that
way. Everything you need is on this page; none of it is onerous.

## The one invariant

Wayfinder's **scored decision path is offline, deterministic, and keyless** — it never calls a model,
touches the network, or reads a credential to make a routing decision, and the same prompt always scores
the same (WF-ADR-0001). The core is stdlib-only and imports no web or SDK code; `import rac` must fail.
Anything that needs the network or a key lives in the optional `gateway` / `ui` layers and is imported
lazily. Keep new work on the right side of that line: if it touches the scored decision, it stays pure.

Python **3.11+**, Apache-2.0.

## Getting set up

```bash
pip install -e ".[dev]"
```

`[dev]` pulls the test runner plus `fastapi` / `httpx` (which exercise the gateway and UI); `rich` and
`textual` are core dependencies, so this one install runs the whole suite.

## The gate — run before every push

```bash
ruff check .
python -m mypy wayfinder_router
python -m pytest -q          # or: make test
```

All three must be clean, and `pytest` should collect the **full** suite (600+ tests). If you see only a
few hundred, read the next paragraph.

> **Run pytest and mypy as `python -m …`, not bare `pytest` / `mypy`.** A `pytest` installed on a
> different interpreter won't see `fastapi` / `textual`, so the gateway/ui/tui tests quietly
> `importorskip` and disappear (and mypy prints spurious `textual.*` "missing stub" errors). That's the
> wrong interpreter, not a real failure — `python -m` uses the one where you installed the package.

## Commits and pull requests

Commit subjects follow **Conventional Commits**:

```
type(scope): imperative summary
```

- **type** — one of `feat`, `fix`, `docs`, `chore`, `test`.
- **scope** — required, single, lowercase: the area you touched (`gateway`, `cli`, `tui`, `ui`,
  `adapter`, `pricing`, `service`, `calibrate`, `suite`, `release`, …). One scope, not `fix(ui,cli)`.
- **summary** — imperative mood, lowercase after the colon, no trailing period.

Every commit needs a **descriptive body** — what changed and why, not a restatement of the subject.

Reference decisions from the body: bracket trailers `[roadmap:WF-ROADMAP-XXXX]`, `[design:WF-DESIGN-XXXX]`,
`[release:X]`, and ADRs inline in prose as `(WF-ADR-XXXX)`.

**No AI attribution** anywhere in commits or PRs — no "Generated with …" footers, no `Co-Authored-By:` bot
trailers, no session links. Use whatever tools you like; just don't sign the bot into the history.

PRs are **squash-merged**, and a maintainer writes the final squash subject (with its `(#NN)`). So your PR
title should already be a clean conventional subject, and the description should explain the change.

## Decision records

Anything that changes behaviour gets a short decision doc alongside the code:

| Kind | Directory | Filename |
|---|---|---|
| Architecture decision | `decisions/` | `WF-ADR-NNNN-slug.md` |
| Design note | `designs/` | `WF-DESIGN-NNNN-slug.md` |
| Roadmap | `roadmaps/` | `WF-ROADMAP-NNNN-slug.md` |

**Numbers are unique and monotonic — take the next free one, never reuse an existing number.** ADRs are at
`0039`, so the next is `0040`. And add a `## Unreleased` entry to [`CHANGELOG.md`](CHANGELOG.md) for
anything users would notice.

## Releases

Releases are cut by maintainers — see [`RELEASE.md`](RELEASE.md). Don't bump `__version__` in a feature
PR; the release commit is the only place it changes.

## Before you open a PR

- [ ] Conventional, single-scope title; descriptive body.
- [ ] `ruff check .`, `python -m mypy wayfinder_router`, `python -m pytest -q` all green.
- [ ] No AI attribution in commits or the PR.
- [ ] Behaviour change → an ADR/design/roadmap doc with the next free number, and a `CHANGELOG.md`
      `## Unreleased` entry.
- [ ] The scored decision path stays offline, deterministic, and keyless (WF-ADR-0001).
