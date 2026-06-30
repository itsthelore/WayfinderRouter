#!/usr/bin/env python3
"""Emit a golden corpus from the REAL Python scorer — the cross-language parity contract.

The shared JS decision core (``clients/shared/src/scorer.js``) is a verified mirror of
``wayfinder_router.complexity.score_complexity``; it ships in the desktop app ONLY as the
decision-only degraded mode (WF-ADR-0042), and only behind this gate. Run from the repo root:

    python3 tools/golden.py > clients/shared/test/golden.json
    node clients/shared/test/parity.mjs   # exits non-zero on any divergence

The prompts are adversarial: the determinism traps (CRLF, fences, frontmatter, math glyphs,
CJK/RTL, the .xx5 rounding tie) live here so a JS divergence fails the build.
"""
import json
import sys
from pathlib import Path

# Importable when wayfinder_router is pip-installed; the path insert covers a bare checkout too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wayfinder_router.complexity import score_complexity  # noqa: E402

PROMPTS = {
    "empty": "",
    "blank_whitespace": "   \n\t  ",
    "short_easy": "fix my typo please",
    "question": "what is the 100th prime number?",
    "headings_lists": "# Plan\n\n## Steps\n- one\n- two\n- three\n1. first\n2. second",
    "code_fence": "Here is code:\n```python\n# a comment that looks like a heading\n- not a list\nx = 1\n```\ndone",
    "unterminated_fence": "start\n```\nstuff\n- still in fence\n# also in fence",
    "tilde_fence": "~~~\ncode\n~~~\nafter",
    "table": "| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |",
    "links": "see [docs](http://x) and [more](http://y) plus [z](http://z)",
    "math_symbols": "show that ∑ x ≤ ∞ and ∫ f dx ≥ 0 using \\alpha and \\beta",
    "reasoning_terms": "prove the theorem by induction and derive the recurrence",
    "constraints": "you must do it without loops, only using exactly one pass",
    "frontmatter": "---\ntitle: x\ntags: [a,b]\n---\n# Real Heading\nbody text here",
    "crlf_endings": "# H1\r\n- item one\r\n- item two\r\n| a | b |\r\n",
    "long_400plus": ("word " * 450).strip(),
    "emoji_cjk": "请证明这个定理 🙏 prove it with 日本語 mixed 😀 text and more words here",
    "rounding_a": "alpha beta gamma " * 7,
    "rounding_b": "# h\n- a\n- b\n- c\ncode block follows\n```\nx\n```\nand a [l](u)",
    "rtl_arabic": "أثبت النظرية prove the theorem باستخدام الاستقراء induction",
    "nbsp_whitespace": "word word word line para tab\tend",
}

out = []
for name, text in PROMPTS.items():
    s = score_complexity(text)
    out.append({
        "name": name, "text": text, "score": s.score,
        "recommendation": s.recommendation, "features": dict(s.features),
    })
print(json.dumps(out, ensure_ascii=False, indent=1))
