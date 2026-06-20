"""Guards for the shipped docs: the FAQ exists, the README links it, and the FAQ's
relative links resolve — so cross-links to the benchmark/decision docs can't silently rot.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_readme_links_the_faq():
    assert (_ROOT / "docs" / "faq.md").is_file()
    assert "docs/faq.md" in (_ROOT / "README.md").read_text(encoding="utf-8")


def test_faq_relative_links_resolve():
    faq = _ROOT / "docs" / "faq.md"
    for target in _LINK.findall(faq.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#")):
            continue
        path = (faq.parent / target.split("#", 1)[0]).resolve()
        assert path.is_file(), f"broken FAQ link: {target}"
