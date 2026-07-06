"""Identity model for the governance spine — one principal per request (WF-DESIGN-0013 §5).

Gives ``VirtualKey.tags`` its first consumer: virtual keys are the v1 identity source, and
every request resolves to exactly one :class:`Principal` or the shared :data:`ANONYMOUS`
principal — attribution is *total*, never ``None`` and never raising on the resolve path
(invariant 10). A :class:`Principal` is the upstream of the audit trail's frozen
``identity_kind``/``team``/``tags`` fields, so its ``kind``/``team``/``tags`` are load-bearing
values a downstream record copies verbatim.

The tag convention (``team:<x>`` -> team, ``kind:<x>`` -> kind, everything else -> residual
tags) is consumed here and nowhere else. Validation of ``kind`` against
:data:`IDENTITY_KINDS` lives at the registry boundary only: :class:`Principal` stays a plain
frozen dataclass so :func:`principal_from_vkey` can synthesize a trusted ``kind:`` value
without raising, while :class:`IdentityRegistry` rejects an unknown ``kind`` at construction
and in :meth:`IdentityRegistry.from_toml`. Pure stdlib, no network, no keys, no model, in
keeping with the offline deterministic core (WF-ADR-0001) and Wayfinder's own logic never
calling out (WF-ADR-0043); config parsing is spec-first and additive (WF-ADR-0044).
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass

ANONYMOUS_ID: str = "anonymous"

# The closed vocabulary of principal kinds; the registry rejects anything outside it.
IDENTITY_KINDS: tuple[str, ...] = ("human", "agent", "service", "anonymous")


class IdentityError(Exception):
    """Raised when identity configuration is invalid (duplicate id, or unknown kind)."""


@dataclass(frozen=True)
class Principal:
    """A resolved request identity: id, kind, optional team, and residual tags.

    Frozen and hashable (``tags`` is a tuple) so principals are usable as set/dict keys. It
    does not self-validate ``kind`` — that check is the registry's responsibility.
    """

    id: str
    kind: str
    team: str | None = None
    tags: tuple[str, ...] = ()


# The shared anonymous principal returned whenever no vkey identifies the request; resolve
# returns this exact object by identity, so callers may compare with ``is``.
ANONYMOUS: Principal = Principal(id=ANONYMOUS_ID, kind="anonymous")


def principal_from_vkey(vkey_id: str, tags: tuple[str, ...]) -> Principal:
    """Synthesize a principal from a vkey id and its tags via the ``team:``/``kind:`` convention.

    Consumes ``team:<x>`` into ``team`` and ``kind:<x>`` into ``kind`` (first occurrence of
    each wins; default kind is ``"human"``); every other tag is kept as a residual tag with
    original order preserved. The synthesized ``kind`` is trusted here and not validated.
    """
    kind: str | None = None
    team: str | None = None
    residual: list[str] = []
    for tag in tags:
        if tag.startswith("kind:"):
            if kind is None:
                kind = tag[len("kind:") :]
        elif tag.startswith("team:"):
            if team is None:
                team = tag[len("team:") :]
        else:
            residual.append(tag)
    return Principal(
        id=vkey_id,
        kind=kind if kind is not None else "human",
        team=team,
        tags=tuple(residual),
    )


class IdentityRegistry:
    """A by-id map of configured principals with a total ``resolve`` attribution rule."""

    def __init__(self, principals: Iterable[Principal]) -> None:
        """Build the by-id map, rejecting duplicate ids and unknown kinds with ``IdentityError``."""
        by_id: dict[str, Principal] = {}
        for principal in principals:
            if principal.kind not in IDENTITY_KINDS:
                raise IdentityError(f"unknown identity kind: {principal.kind!r}")
            if principal.id in by_id:
                raise IdentityError(f"duplicate principal id: {principal.id!r}")
            by_id[principal.id] = principal
        self._by_id = by_id

    def resolve(
        self, *, vkey_id: str | None, vkey_tags: tuple[str, ...] = ()
    ) -> Principal:
        """Resolve a request to exactly one principal: anonymous, configured, or synthesized.

        Precedence: a ``None`` vkey is the :data:`ANONYMOUS` singleton (tags do not rescue it);
        a configured vkey id wins over synthesis; an unmapped id synthesizes from its tags.
        """
        if vkey_id is None:
            return ANONYMOUS
        configured = self._by_id.get(vkey_id)
        if configured is not None:
            return configured
        return principal_from_vkey(vkey_id, vkey_tags)

    def get(self, principal_id: str) -> Principal | None:
        """Return the configured principal for ``principal_id``, or ``None`` if absent."""
        return self._by_id.get(principal_id)

    @classmethod
    def from_toml(cls, text: str) -> IdentityRegistry:
        """Parse ``[identity.principals.<id>]`` tables into a registry from their explicit fields.

        Reads ``kind`` (required), ``team`` (optional -> ``None``), and ``tags`` (optional list
        -> tuple, missing -> ``()``) directly; it does not run the tag convention. The
        ``[identity].enabled`` key and any non-principal keys are ignored.
        """
        data = tomllib.loads(text)
        principals_table = data.get("identity", {}).get("principals", {})
        principals = [
            Principal(
                id=pid,
                kind=entry["kind"],
                team=entry.get("team"),
                tags=tuple(entry.get("tags", ())),
            )
            for pid, entry in principals_table.items()
        ]
        return cls(principals)
