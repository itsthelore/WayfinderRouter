"""Tests for Wayfinder's own config loader (wayfinder-router.toml, no RAC)."""

from __future__ import annotations

import pytest
from wayfinder_router.complexity import DEFAULT_THRESHOLD
from wayfinder_router.config import CONFIG_PATH_ENV, THRESHOLD_ENV, find_config_file

from wayfinder_router import RoutingConfig, WayfinderConfigError, load_routing_config


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(THRESHOLD_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)


def _write(tmp_path, body: str) -> str:
    (tmp_path / "wayfinder-router.toml").write_text(body, encoding="utf-8")
    return str(tmp_path)


def _clf_toml(models: str, intercepts: str, weights: str) -> str:
    return (
        "[routing.classifier]\n"
        f"models = {models}\n"
        f"intercepts = {intercepts}\n\n"
        "[routing.classifier.weights]\n"
        f"{weights}\n"
    )


# --- defaults + binary threshold --------------------------------------------


def test_no_config_yields_default_binary(tmp_path):
    config = load_routing_config(str(tmp_path))
    assert config.classifier is None
    assert config.tiers[0].model == "local"
    assert config.tiers[1].min_score == DEFAULT_THRESHOLD


def test_threshold_sets_the_binary_cut(tmp_path):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    config = load_routing_config(start)
    assert config.tiers == RoutingConfig.binary(0.8).tiers


def test_env_overrides_file_threshold(tmp_path, monkeypatch):
    start = _write(tmp_path, "[routing]\nthreshold = 0.8\n")
    monkeypatch.setenv(THRESHOLD_ENV, "0.2")
    assert load_routing_config(start).tiers[1].min_score == 0.2


def test_weights_merge_over_defaults(tmp_path):
    start = _write(tmp_path, "[routing]\nweights = { word_count = 9.0 }\n")
    config = load_routing_config(start)
    assert config.weights["word_count"] == 9.0
    assert config.weights["heading_count"] == RoutingConfig().weights["heading_count"]


def test_config_is_discovered_by_walking_up(tmp_path):
    _write(tmp_path, "[routing]\nthreshold = 0.9\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert load_routing_config(str(nested)).tiers[1].min_score == 0.9


# --- WAYFINDER_CONFIG override (WF-ADR-0042) --------------------------------


def test_wayfinder_config_env_overrides_walk_up(tmp_path, monkeypatch):
    # An explicit WAYFINDER_CONFIG wins over any wayfinder-router.toml found by walking up.
    walked = tmp_path / "walked"
    walked.mkdir()
    _write(walked, "[routing]\nthreshold = 0.9\n")  # would be found by the walk-up
    chosen = tmp_path / "elsewhere" / "wayfinder-router.toml"
    chosen.parent.mkdir()
    chosen.write_text("[routing]\nthreshold = 0.2\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(chosen))
    assert find_config_file(str(walked)).samefile(chosen)
    assert load_routing_config(str(walked)).tiers[1].min_score == 0.2


def test_wayfinder_config_env_missing_file_is_none_not_walk_up(tmp_path, monkeypatch):
    # A configured-but-absent override is a clear None, never a silent walk-up to another file.
    _write(tmp_path, "[routing]\nthreshold = 0.9\n")  # present, but must be ignored
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "does-not-exist.toml"))
    assert find_config_file(str(tmp_path)) is None
    assert load_routing_config(str(tmp_path)).tiers[1].min_score == DEFAULT_THRESHOLD


def test_wayfinder_config_unset_uses_walk_up(tmp_path):
    # With the env unset (the autouse fixture clears it), behaviour is the unchanged walk-up.
    _write(tmp_path, "[routing]\nthreshold = 0.77\n")
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    assert find_config_file(str(nested)).samefile(tmp_path / "wayfinder-router.toml")


# --- tiers ------------------------------------------------------------------


def test_tiers_must_be_declared_in_ascending_order(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.6\nmodel = \"large\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"medium\"\n"
    )
    with pytest.raises(WayfinderConfigError, match="first tier must have min_score = 0.0"):
        load_routing_config(_write(tmp_path, body))


@pytest.mark.parametrize(
    "body",
    [
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"m\"\n",  # no 0.0 tier
        (
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"a\"\n\n"
            "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"b\"\n"
        ),  # duplicate
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"\"\n",  # empty model
        "[[routing.tiers]]\nmin_score = 2.0\nmodel = \"a\"\n",  # out of range (high)
        "[[routing.tiers]]\nmin_score = -0.1\nmodel = \"a\"\n",  # out of range (negative)
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"a\"\ncost = -1.0\n",  # negative cost
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"a\"\ncost = \"free\"\n",  # non-number cost
    ],
)
def test_malformed_tiers_are_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


def test_optional_tier_cost_is_parsed(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"local\"\ncost = 0.2\n\n"
        "[[routing.tiers]]\nmin_score = 0.4\nmodel = \"cloud\"\ncost = 1.0\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert [t.cost for t in config.tiers] == [0.2, 1.0]


def test_tier_cost_is_optional(tmp_path):
    # A tier without cost keeps cost None — the metadata is purely opt-in.
    body = "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"local\"\n"
    config = load_routing_config(_write(tmp_path, body))
    assert config.tiers[0].cost is None


# --- classifier -------------------------------------------------------------


def test_classifier_is_parsed(tmp_path):
    body = (
        "[routing.classifier]\n"
        'models = ["local", "cloud"]\n'
        "intercepts = [0.5, -0.5]\n\n"
        "[routing.classifier.weights]\n"
        "word_count = [0.0, 2.0]\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is not None
    assert config.classifier.models == ("local", "cloud")
    assert config.classifier.weights["word_count"] == (0.0, 2.0)
    # Unspecified features default to a zero vector of the right width.
    assert config.classifier.weights["heading_count"] == (0.0, 0.0)


def test_classifier_takes_precedence_over_tiers(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"x\"\n\n"
        "[routing.classifier]\n"
        'models = ["local", "cloud"]\n'
        "intercepts = [0.0, 0.0]\n\n"
        "[routing.classifier.weights]\n"
        "word_count = [0.0, 1.0]\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is not None


@pytest.mark.parametrize(
    "body",
    [
        _clf_toml('["only"]', "[0.0]", "word_count = [0.0]"),  # <2 models
        _clf_toml('["a", "b"]', "[0.0]", "word_count = [0.0, 1.0]"),  # intercepts wrong length
        _clf_toml('["a", "b"]', "[0.0, 0.0]", "bogus = [0.0, 1.0]"),  # unknown feature
        _clf_toml('["a", "b"]', "[0.0, 0.0]", "word_count = [0.0]"),  # weight wrong length
    ],
)
def test_malformed_classifier_is_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


# --- threshold validation ---------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "[routing]\nthreshold = 2.0\n",
        '[routing]\nthreshold = "high"\n',
        "routing = 1\n",
        "[routing]\nweights = { word_count = -1.0 }\n",
    ],
)
def test_malformed_config_is_rejected(tmp_path, body):
    with pytest.raises(WayfinderConfigError):
        load_routing_config(_write(tmp_path, body))


def test_malformed_env_threshold_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(THRESHOLD_ENV, "nope")
    with pytest.raises(WayfinderConfigError):
        load_routing_config(str(tmp_path))


# --- configurable lexicon (WF-ADR-0019) -------------------------------------


def test_lexicon_terms_parse_and_lower_case(tmp_path):
    body = (
        "[routing]\nthreshold = 0.1\n\n"
        "[routing.lexicon]\n"
        'reasoning_terms = ["Differential", "contraindication"]\n'
    )
    config = load_routing_config(_write(tmp_path, body))
    assert "differential" in config.lexicon.reasoning_terms  # lower-cased to match the scanner
    assert "contraindication" in config.lexicon.reasoning_terms
    # an omitted family keeps its built-in default
    from wayfinder_router.complexity import DEFAULT_LEXICON
    assert config.lexicon.constraint_terms == DEFAULT_LEXICON.constraint_terms


def test_lexicon_round_trips_and_default_is_omitted():
    from wayfinder_router import Lexicon, dump_routing_toml, routing_config_from_toml
    from wayfinder_router.complexity import DEFAULT_WEIGHTS

    lex = Lexicon(reasoning_terms=frozenset({"tort", "estoppel"}))
    cfg = RoutingConfig(weights=dict(DEFAULT_WEIGHTS), lexicon=lex)
    dumped = dump_routing_toml(cfg)
    assert "[routing.lexicon]" in dumped
    assert routing_config_from_toml(dumped).lexicon == lex
    # a default-lexicon config emits no lexicon block
    assert "[routing.lexicon]" not in dump_routing_toml(RoutingConfig())


def test_custom_lexicon_is_off_until_weighted():
    # Off-by-default (WF-ADR-0016): custom terms count, but at weight 0.0 they change nothing.
    from wayfinder_router import Lexicon, score_complexity
    from wayfinder_router.complexity import DEFAULT_WEIGHTS, binary_tiers

    lex = Lexicon(reasoning_terms=frozenset({"indemnify"}))
    weights = dict(DEFAULT_WEIGHTS)
    prompt = "Please indemnify the party."
    base = score_complexity(prompt, config=RoutingConfig(weights=weights, tiers=binary_tiers(0.05)))
    cust = score_complexity(
        prompt, config=RoutingConfig(weights=weights, tiers=binary_tiers(0.05), lexicon=lex)
    )
    assert base.recommendation == cust.recommendation  # weight 0.0 -> no effect
    # raising the weight makes the custom term escalate where the default lexicon would not
    hot = dict(DEFAULT_WEIGHTS, reasoning_term_count=8.0)
    on = score_complexity(prompt, config=RoutingConfig(weights=hot, tiers=binary_tiers(0.05), lexicon=lex))
    off = score_complexity(prompt, config=RoutingConfig(weights=hot, tiers=binary_tiers(0.05)))
    assert on.recommendation == "cloud" and off.recommendation == "local"


def test_invalid_lexicon_is_rejected(tmp_path):
    for body in (
        '[routing.lexicon]\nreasoning_terms = "tort"\n',          # not a list
        '[routing.lexicon]\nreasoning_terms = ["ok", ""]\n',      # empty term
        '[routing.lexicon]\nunknown_family = ["x"]\n',            # unknown key
    ):
        with pytest.raises(WayfinderConfigError):
            load_routing_config(_write(tmp_path, body))


# ---------------------------------------------------------------------- set_toml_bool (the seam)


def test_set_toml_bool_replaces_in_place_preserving_everything_else():
    from wayfinder_router.config import set_toml_bool

    text = (
        "# top comment\n"
        "[routing]\n"
        "threshold = 0.08\n"
        "\n"
        "[gateway]\n"
        "# offline keeps everything local (WF-ADR-0039)\n"
        "offline = false\n"
        "timeout = 30\n"
    )
    out = set_toml_bool(text, "gateway", "offline", True)
    assert "offline = true\n" in out
    # every other line survives byte-for-byte
    for line in text.splitlines(keepends=True):
        if not line.startswith("offline"):
            assert line in out
    # flipping back round-trips to the original
    assert set_toml_bool(out, "gateway", "offline", False) == text


def test_set_toml_bool_inserts_under_an_existing_header():
    from wayfinder_router.config import set_toml_bool

    text = "[gateway]\ntimeout = 30\n\n[gateway.models.local]\nbase_url = \"http://x\"\nmodel = \"m\"\n"
    out = set_toml_bool(text, "gateway", "offline", True)
    assert out.startswith("[gateway]\noffline = true\ntimeout = 30\n")
    # the sub-table is untouched — [gateway.models.local] must never match [gateway]
    assert '[gateway.models.local]\nbase_url = "http://x"\nmodel = "m"\n' in out


def test_gateway_edit_preserves_typed_apple_provider_block_byte_for_byte():
    from wayfinder_router.config import set_toml_bool

    apple = (
        "[gateway.models.apple-local]\n"
        'provider = "apple-foundation-models" # native, not HTTP\n'
        'model = "system-default"\n'
        'tier = "local"\n'
    )
    out = set_toml_bool(apple, "gateway", "offline", True)
    assert apple in out


def test_set_toml_bool_appends_a_missing_section_and_still_parses():
    from wayfinder_router import bootstrap
    from wayfinder_router.config import routing_config_from_toml, set_toml_bool
    from wayfinder_router.gateway import gateway_config_from_toml

    # The shipped presets have [gateway.models.*] blocks but no bare [gateway] — the appended
    # super-table-after-sub-table form must be TOML the real parsers accept.
    for name in ("hybrid", "openai", "gemini"):
        text = bootstrap.render_config(bootstrap.PRESETS[name])
        out = set_toml_bool(text, "gateway", "offline", True)
        assert out.endswith("\n[gateway]\noffline = true\n")
        assert gateway_config_from_toml(out).offline is True
        routing_config_from_toml(out)  # must not raise
    apple = bootstrap.render_config(bootstrap.PRESETS["apple-local"])
    out = set_toml_bool(apple, "gateway", "offline", True)
    assert "[gateway]\noffline = true\n" in out
    assert gateway_config_from_toml(out).offline is True
    routing_config_from_toml(out)


def test_set_toml_bool_ignores_commented_keys():
    from wayfinder_router.config import set_toml_bool

    text = "[gateway]\n# offline = false\n"
    out = set_toml_bool(text, "gateway", "offline", True)
    # the commented example survives; a real key is inserted under the header
    assert "# offline = false\n" in out
    assert out.startswith("[gateway]\noffline = true\n")
