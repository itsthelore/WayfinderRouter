//! Stock starter lexicons, ported byte-for-byte from `wayfinder_router.profiles`.
//!
//! These are calibration seeds, not validated routers. `curated` profiles are
//! hand-authored; `mined` profiles come from RouterBench and retain the Python
//! module's cautionary provenance notes.

use serde::Serialize;

/// A named starter lexicon for the optional lexical signals.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub struct LexiconProfile {
    /// Stable profile identifier.
    pub id: &'static str,
    /// Human-readable profile name.
    pub name: &'static str,
    /// Provenance category: `curated` or `mined`.
    pub source: &'static str,
    /// Lowercase single-token reasoning vocabulary.
    pub reasoning_terms: &'static [&'static str],
    /// Lowercase single-token constraint vocabulary.
    pub constraint_terms: &'static [&'static str],
    /// Provenance and calibration guidance.
    pub note: &'static str,
}

const REAL: &str =
    "Mined from RouterBench: real subject-matter vocabulary; still calibrate on your traffic.";
const WEAK: &str = "Mined from RouterBench word-problem tasks: task-surface vocabulary, NOT difficulty — a cautionary example, not a recommendation.";

/// Profiles in the same stable order as Python's `PROFILES` tuple.
pub const PROFILES: [LexiconProfile; 10] = [
    LexiconProfile {
        id: "proofs-math",
        name: "Proofs & mathematics",
        source: "curated",
        reasoning_terms: &[
            "prove",
            "proof",
            "proofs",
            "theorem",
            "lemma",
            "corollary",
            "axiom",
            "conjecture",
            "induction",
            "contradiction",
            "qed",
            "derive",
            "derivation",
            "integral",
            "derivative",
            "eigenvalue",
            "asymptotic",
            "bijection",
            "isomorphism",
            "modulo",
            "recurrence",
            "polynomial",
            "monotonic",
            "invariant",
            "optimal",
            "optimality",
        ],
        constraint_terms: &["exactly", "minimize", "maximize", "subject"],
        note: "Hand-authored maths/CS reasoning vocabulary (close to the built-in default).",
    },
    LexiconProfile {
        id: "law-compliance",
        name: "Law & compliance",
        source: "curated",
        reasoning_terms: &[
            "liable",
            "liability",
            "indemnify",
            "indemnification",
            "pursuant",
            "herein",
            "hereto",
            "whereas",
            "statute",
            "statutory",
            "jurisdiction",
            "plaintiff",
            "defendant",
            "tort",
            "breach",
            "covenant",
            "waiver",
            "arbitration",
            "negligence",
            "damages",
            "contractual",
        ],
        constraint_terms: &[
            "shall",
            "must",
            "prohibited",
            "required",
            "notwithstanding",
            "provided",
        ],
        note: "Hand-authored legal/compliance vocabulary.",
    },
    LexiconProfile {
        id: "code-infra",
        name: "Code & infrastructure",
        source: "curated",
        reasoning_terms: &[
            "concurrency",
            "concurrent",
            "deadlock",
            "mutex",
            "idempotent",
            "idempotency",
            "latency",
            "throughput",
            "distributed",
            "consensus",
            "replication",
            "sharding",
            "rollback",
            "migration",
            "schema",
            "consistency",
            "atomicity",
            "serializable",
            "partition",
            "race",
            "lock",
        ],
        constraint_terms: &[],
        note: "Hand-authored systems/infrastructure vocabulary.",
    },
    LexiconProfile {
        id: "science-medicine",
        name: "Science & medicine",
        source: "curated",
        reasoning_terms: &[
            "hypothesis",
            "pathogenesis",
            "etiology",
            "diagnosis",
            "prognosis",
            "cardiac",
            "hepatic",
            "renal",
            "membrane",
            "enzyme",
            "mitochondria",
            "pyruvate",
            "catalysis",
            "molecule",
            "atom",
            "orbital",
            "electron",
            "isotope",
            "contraindication",
            "dosage",
            "pharmacokinetics",
        ],
        constraint_terms: &[],
        note: "Hand-authored science/medicine vocabulary.",
    },
    LexiconProfile {
        id: "mined-science",
        name: "Science (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "hypertension",
            "center",
            "learning",
            "objects",
            "cardiac",
            "pyruvate",
            "mild",
            "parents",
            "phase",
            "region",
            "products",
            "membrane",
            "anterior",
            "element",
            "orbit",
            "chain",
            "atoms",
            "neck",
            "rapid",
            "potential",
        ],
        constraint_terms: &[],
        note: REAL,
    },
    LexiconProfile {
        id: "mined-general",
        name: "General knowledge (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "committee",
            "planning",
            "taxes",
            "measure",
            "identity",
            "punishment",
            "procedures",
            "cultural",
            "industry",
            "areas",
            "ethics",
            "organization",
            "share",
            "falls",
            "local",
            "skills",
            "curve",
            "identify",
            "unemployment",
            "spending",
        ],
        constraint_terms: &[],
        note: REAL,
    },
    LexiconProfile {
        id: "mined-humanities",
        name: "Humanities (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "classes",
            "function",
            "expression",
            "extension",
            "latin",
            "russia",
            "yard",
            "facilities",
            "famous",
            "republics",
            "settlement",
            "socialist",
            "materials",
            "morality",
            "western",
            "colonial",
            "fallacy",
            "consequences",
            "cultural",
            "nations",
        ],
        constraint_terms: &[],
        note: REAL,
    },
    LexiconProfile {
        id: "mined-commonsense",
        name: "Commonsense (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "carry",
            "sheet",
            "morally",
            "scenarios",
            "scenario",
            "standards",
            "moral",
            "character",
            "ordinary",
            "wrong",
            "buying",
            "wedding",
            "oven",
            "major",
            "adjust",
            "growth",
        ],
        constraint_terms: &[],
        note: WEAK,
    },
    LexiconProfile {
        id: "mined-math",
        name: "Math word-problems (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "candy", "sunday", "mother", "saturday", "mile", "weighs", "cards", "birthday",
        ],
        constraint_terms: &[],
        note: WEAK,
    },
    LexiconProfile {
        id: "mined-multilingual",
        name: "Multilingual (RouterBench)",
        source: "mined",
        reasoning_terms: &[
            "dragon",
            "animal",
            "approximate",
            "birth",
            "digit",
            "estimated",
            "exact",
            "guesses",
            "sentences",
            "subject's",
            "wishing",
            "without",
            "year",
            "zodiac",
            "zones",
            "monkey",
            "chinese",
            "other",
            "translate",
            "snake",
        ],
        constraint_terms: &[],
        note: WEAK,
    },
];

/// Return all stock profiles in compatibility order.
#[must_use]
pub const fn profiles() -> &'static [LexiconProfile] {
    &PROFILES
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{REAL, WEAK, profiles};

    #[test]
    fn profile_order_and_provenance_match_python() {
        let profiles = profiles();
        assert_eq!(
            profiles
                .iter()
                .map(|profile| profile.id)
                .collect::<Vec<_>>(),
            [
                "proofs-math",
                "law-compliance",
                "code-infra",
                "science-medicine",
                "mined-science",
                "mined-general",
                "mined-humanities",
                "mined-commonsense",
                "mined-math",
                "mined-multilingual",
            ]
        );
        assert!(
            profiles[..4]
                .iter()
                .all(|profile| profile.source == "curated")
        );
        assert!(
            profiles[4..]
                .iter()
                .all(|profile| profile.source == "mined")
        );
        assert!(profiles[4..7].iter().all(|profile| profile.note == REAL));
        assert!(profiles[7..].iter().all(|profile| profile.note == WEAK));
    }

    #[test]
    fn serialization_shape_and_terms_match_python() -> Result<(), serde_json::Error> {
        let value = serde_json::to_value(profiles())?;
        assert_eq!(
            value[0],
            json!({
                "id": "proofs-math",
                "name": "Proofs & mathematics",
                "source": "curated",
                "reasoning_terms": ["prove", "proof", "proofs", "theorem", "lemma", "corollary", "axiom", "conjecture", "induction", "contradiction", "qed", "derive", "derivation", "integral", "derivative", "eigenvalue", "asymptotic", "bijection", "isomorphism", "modulo", "recurrence", "polynomial", "monotonic", "invariant", "optimal", "optimality"],
                "constraint_terms": ["exactly", "minimize", "maximize", "subject"],
                "note": "Hand-authored maths/CS reasoning vocabulary (close to the built-in default)."
            })
        );
        assert_eq!(value[9]["reasoning_terms"][9], "subject's");
        assert_eq!(value[9]["constraint_terms"], json!([]));
        Ok(())
    }
}
