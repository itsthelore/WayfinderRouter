"""Stock lexicon profiles (WF-ADR-0024): starting vocabularies for the opt-in
lexical signals (WF-ADR-0016 / WF-ADR-0019).

Pick a profile to seed ``[routing.lexicon]``, raise the lexical weight, then
*calibrate on your own labels* — these are starting points, not validated routers.
Two provenances, both honestly labelled:

- ``curated``: hand-authored, defensible domain vocabulary. Unvalidated (no benchmark
  behind it) — a sensible head-start, nothing more.
- ``mined``: term lists mined from RouterBench labelled traffic (smoothed log-odds on
  a held-out split). Real provenance, but mixed quality — some domains surface task
  vocabulary rather than difficulty; each carries a ``note`` saying so.

Terms are single lowercase word tokens: the scorer tokenizes on words, so phrases
won't match here, and math symbols are a separate feature (not lexicon terms).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LexiconProfile:
    """A named starter lexicon for the lexical signals."""

    id: str
    name: str
    source: str  # "curated" | "mined"
    reasoning_terms: tuple[str, ...] = ()
    constraint_terms: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "reasoning_terms": list(self.reasoning_terms),
            "constraint_terms": list(self.constraint_terms),
            "note": self.note,
        }


# --- curated, hand-authored (defensible, unvalidated) -----------------------

CURATED: tuple[LexiconProfile, ...] = (
    LexiconProfile(
        id="proofs-math",
        name="Proofs & mathematics",
        source="curated",
        reasoning_terms=(
            "prove", "proof", "proofs", "theorem", "lemma", "corollary", "axiom",
            "conjecture", "induction", "contradiction", "qed", "derive", "derivation",
            "integral", "derivative", "eigenvalue", "asymptotic", "bijection",
            "isomorphism", "modulo", "recurrence", "polynomial", "monotonic",
            "invariant", "optimal", "optimality",
        ),
        constraint_terms=("exactly", "minimize", "maximize", "subject"),
        note="Hand-authored maths/CS reasoning vocabulary (close to the built-in default).",
    ),
    LexiconProfile(
        id="law-compliance",
        name="Law & compliance",
        source="curated",
        reasoning_terms=(
            "liable", "liability", "indemnify", "indemnification", "pursuant", "herein",
            "hereto", "whereas", "statute", "statutory", "jurisdiction", "plaintiff",
            "defendant", "tort", "breach", "covenant", "waiver", "arbitration",
            "negligence", "damages", "contractual",
        ),
        constraint_terms=("shall", "must", "prohibited", "required", "notwithstanding", "provided"),
        note="Hand-authored legal/compliance vocabulary.",
    ),
    LexiconProfile(
        id="code-infra",
        name="Code & infrastructure",
        source="curated",
        reasoning_terms=(
            "concurrency", "concurrent", "deadlock", "mutex", "idempotent", "idempotency",
            "latency", "throughput", "distributed", "consensus", "replication", "sharding",
            "rollback", "migration", "schema", "consistency", "atomicity", "serializable",
            "partition", "race", "lock",
        ),
        note="Hand-authored systems/infrastructure vocabulary.",
    ),
    LexiconProfile(
        id="science-medicine",
        name="Science & medicine",
        source="curated",
        reasoning_terms=(
            "hypothesis", "pathogenesis", "etiology", "diagnosis", "prognosis", "cardiac",
            "hepatic", "renal", "membrane", "enzyme", "mitochondria", "pyruvate", "catalysis",
            "molecule", "atom", "orbital", "electron", "isotope", "contraindication", "dosage",
            "pharmacokinetics",
        ),
        note="Hand-authored science/medicine vocabulary.",
    ),
)


# --- mined from RouterBench (real provenance, mixed quality) -----------------
# Embedded from benchmarks/seed/domain-lexicons.toml so the library carries no
# benchmark dependency. Reproduce with: python -m benchmarks.mine_lexicon ...

_REAL = "Mined from RouterBench: real subject-matter vocabulary; still calibrate on your traffic."
_WEAK = "Mined from RouterBench word-problem tasks: task-surface vocabulary, NOT difficulty — a cautionary example, not a recommendation."

MINED: tuple[LexiconProfile, ...] = (
    LexiconProfile(
        id="mined-science", name="Science (RouterBench)", source="mined", note=_REAL,
        reasoning_terms=(
            "hypertension", "center", "learning", "objects", "cardiac", "pyruvate", "mild",
            "parents", "phase", "region", "products", "membrane", "anterior", "element",
            "orbit", "chain", "atoms", "neck", "rapid", "potential",
        ),
    ),
    LexiconProfile(
        id="mined-general", name="General knowledge (RouterBench)", source="mined", note=_REAL,
        reasoning_terms=(
            "committee", "planning", "taxes", "measure", "identity", "punishment", "procedures",
            "cultural", "industry", "areas", "ethics", "organization", "share", "falls", "local",
            "skills", "curve", "identify", "unemployment", "spending",
        ),
    ),
    LexiconProfile(
        id="mined-humanities", name="Humanities (RouterBench)", source="mined", note=_REAL,
        reasoning_terms=(
            "classes", "function", "expression", "extension", "latin", "russia", "yard",
            "facilities", "famous", "republics", "settlement", "socialist", "materials",
            "morality", "western", "colonial", "fallacy", "consequences", "cultural", "nations",
        ),
    ),
    LexiconProfile(
        id="mined-commonsense", name="Commonsense (RouterBench)", source="mined", note=_WEAK,
        reasoning_terms=(
            "carry", "sheet", "morally", "scenarios", "scenario", "standards", "moral",
            "character", "ordinary", "wrong", "buying", "wedding", "oven", "major", "adjust",
            "growth",
        ),
    ),
    LexiconProfile(
        id="mined-math", name="Math word-problems (RouterBench)", source="mined", note=_WEAK,
        reasoning_terms=("candy", "sunday", "mother", "saturday", "mile", "weighs", "cards", "birthday"),
    ),
    LexiconProfile(
        id="mined-multilingual", name="Multilingual (RouterBench)", source="mined", note=_WEAK,
        reasoning_terms=(
            "dragon", "animal", "approximate", "birth", "digit", "estimated", "exact", "guesses",
            "sentences", "subject's", "wishing", "without", "year", "zodiac", "zones", "monkey",
            "chinese", "other", "translate", "snake",
        ),
    ),
)

PROFILES: tuple[LexiconProfile, ...] = CURATED + MINED
PROFILES_BY_ID: dict[str, LexiconProfile] = {p.id: p for p in PROFILES}
