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


def test_tiers_are_parsed_and_sorted(tmp_path):
    body = (
        "[[routing.tiers]]\nmin_score = 0.6\nmodel = \"large\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.0\nmodel = \"small\"\n\n"
        "[[routing.tiers]]\nmin_score = 0.3\nmodel = \"medium\"\n"
    )
    config = load_routing_config(_write(tmp_path, body))
    assert config.classifier is None
    assert [t.model for t in config.tiers] == ["small", "medium", "large"]
    assert [t.min_score for t in config.tiers] == [0.0, 0.3, 0.6]


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


def test_set_toml_bool_ignores_commented_keys():
    from wayfinder_router.config import set_toml_bool

    text = "[gateway]\n# offline = false\n"
    out = set_toml_bool(text, "gateway", "offline", True)
    # the commented example survives; a real key is inserted under the header
    assert "# offline = false\n" in out
    assert out.startswith("[gateway]\noffline = true\n")


# ------------------------------------------------------------------- add_model_table (the seam)


def test_add_model_table_appends_a_new_table_preserving_everything_else():
    from wayfinder_router.config import add_model_table

    text = "[gateway]\ntimeout = 30\n\n[gateway.models.local]\nbase_url = \"http://x\"\nmodel = \"m\"\n"
    out = add_model_table(
        text, "anthropic-opus", base_url="https://api.anthropic.com/v1", model="claude-opus-4-1",
        api_key_env="ANTHROPIC_API_KEY", api_key_cmd="security find-generic-password -w",
        cost_per_1k=0.015,
    )
    for line in text.splitlines(keepends=True):
        assert line in out  # every existing line survives byte-for-byte
    assert out.endswith(
        "[gateway.models.anthropic-opus]\n"
        'base_url = "https://api.anthropic.com/v1"\n'
        'model = "claude-opus-4-1"\n'
        'api_key_env = "ANTHROPIC_API_KEY"\n'
        'api_key_cmd = "security find-generic-password -w"\n'
        "cost_per_1k = 0.015\n"
    )


def test_add_model_table_minimal_fields_only():
    from wayfinder_router.config import add_model_table

    out = add_model_table(
        "[gateway]\ntimeout = 30\n", "ollama", base_url="http://localhost:11434/v1", model="llama3.3",
    )
    assert out == (
        "[gateway]\ntimeout = 30\n\n[gateway.models.ollama]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "llama3.3"\n'
    )


def test_add_model_table_rejects_a_name_collision():
    from wayfinder_router.config import WayfinderConfigError, add_model_table

    text = '[gateway.models.mine]\nbase_url = "http://x"\nmodel = "m"\n'
    with pytest.raises(WayfinderConfigError, match="already exists"):
        add_model_table(text, "mine", base_url="http://y", model="n")


def test_add_model_table_escapes_quotes_and_backslashes():
    from wayfinder_router.config import add_model_table

    out = add_model_table("", "x", base_url="http://x", model='weird"model\\name')
    assert 'model = "weird\\"model\\\\name"\n' in out


def test_add_model_table_round_trips_through_the_real_parsers():
    from wayfinder_router import bootstrap
    from wayfinder_router.config import add_model_table, routing_config_from_toml
    from wayfinder_router.gateway import gateway_config_from_toml

    for name in ("hybrid", "openai", "gemini"):
        text = bootstrap.render_config(bootstrap.PRESETS[name])
        out = add_model_table(
            text, "extra", base_url="https://example.test/v1", model="m", api_key_env="EXTRA_API_KEY",
        )
        gw = gateway_config_from_toml(out)
        assert gw.models["extra"].model == "m"
        assert gw.models["extra"].api_key_env == "EXTRA_API_KEY"
        routing_config_from_toml(out)  # must not raise — new model isn't in any tier


# ---------------------------------------------------------------- set_toml_string_list (the seam)


def test_set_toml_string_list_replaces_in_place_preserving_everything_else():
    from wayfinder_router.config import set_toml_string_list

    text = (
        "[gateway.models.cloud]\n"
        'base_url = "http://x"\n'
        'model = "m"\n'
        "fallbacks = [\"old\"]\n"
        "cost_per_1k = 0.01\n"
    )
    out = set_toml_string_list(text, "gateway.models.cloud", "fallbacks", ["local"])
    assert 'fallbacks = ["local"]\n' in out
    assert 'cost_per_1k = 0.01\n' in out  # unrelated line survives byte-for-byte


def test_set_toml_string_list_inserts_under_an_existing_header():
    from wayfinder_router.config import set_toml_string_list

    text = '[gateway.models.cloud]\nbase_url = "http://x"\nmodel = "m"\n'
    out = set_toml_string_list(text, "gateway.models.cloud", "fallbacks", ["local", "backup"])
    assert out == (
        '[gateway.models.cloud]\nfallbacks = ["local", "backup"]\nbase_url = "http://x"\nmodel = "m"\n'
    )


def test_set_toml_string_list_empty_list_clears_rather_than_removes():
    from wayfinder_router.config import set_toml_string_list

    text = '[gateway.models.cloud]\nfallbacks = ["local"]\n'
    out = set_toml_string_list(text, "gateway.models.cloud", "fallbacks", [])
    assert out == '[gateway.models.cloud]\nfallbacks = []\n'


def test_set_toml_string_list_escapes_values():
    from wayfinder_router.config import set_toml_string_list

    out = set_toml_string_list("", "gateway.models.cloud", "fallbacks", ['weird"name'])
    assert 'fallbacks = ["weird\\"name"]\n' in out


# ------------------------------------------------------------------- set_tier_min_score (the seam)


def test_set_tier_min_score_replaces_in_place_preserving_everything_else():
    from wayfinder_router.config import set_tier_min_score

    text = (
        "[[routing.tiers]]\n"
        "min_score = 0.0\n"
        'model = "local"\n'
        "\n"
        "[[routing.tiers]]\n"
        'model = "cloud"\n'
        "min_score = 0.6\n"
        "cost = 1.0\n"
    )
    out = set_tier_min_score(text, "cloud", 0.45)
    assert "min_score = 0.45\n" in out
    assert 'model = "local"\n' in out
    assert "min_score = 0.0\n" in out  # the other tier's line is untouched
    assert "cost = 1.0\n" in out


def test_set_tier_min_score_inserts_a_missing_key():
    from wayfinder_router.config import set_tier_min_score

    text = '[[routing.tiers]]\nmodel = "local"\n'
    out = set_tier_min_score(text, "local", 0.0)
    assert out == '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n'


def test_set_tier_min_score_rejects_an_unknown_model():
    from wayfinder_router.config import WayfinderConfigError, set_tier_min_score

    text = '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n'
    with pytest.raises(WayfinderConfigError, match="no '\\[\\[routing.tiers\\]\\]' entry"):
        set_tier_min_score(text, "cloud", 0.5)


def test_set_tier_min_score_rejects_when_no_tiers_exist():
    from wayfinder_router.config import WayfinderConfigError, set_tier_min_score

    with pytest.raises(WayfinderConfigError, match="no '\\[\\[routing.tiers\\]\\]' entries"):
        set_tier_min_score("[routing]\nthreshold = 0.5\n", "local", 0.0)


def test_set_tier_min_score_round_trips_through_the_real_parser():
    from wayfinder_router.config import routing_config_from_toml, set_tier_min_score

    text = '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n\n[[routing.tiers]]\nmin_score = 0.6\nmodel = "cloud"\n'
    out = set_tier_min_score(text, "cloud", 0.3)
    routing = routing_config_from_toml(out)
    assert [(t.min_score, t.model) for t in routing.tiers] == [(0.0, "local"), (0.3, "cloud")]


def test_set_tier_min_score_a_breaking_edit_still_writes_but_reparse_catches_it():
    # The primitive itself is pure text surgery — it's the CLI/caller's job to re-parse and
    # refuse to write on a monotonicity violation (belt and braces, same as every seam verb).
    from wayfinder_router import WayfinderConfigError
    from wayfinder_router.config import routing_config_from_toml, set_tier_min_score

    text = (
        '[[routing.tiers]]\nmin_score = 0.0\nmodel = "local"\n\n'
        '[[routing.tiers]]\nmin_score = 0.6\nmodel = "cloud"\n'
    )
    out = set_tier_min_score(text, "cloud", 0.0)  # ties local's 0.0 — no longer strictly ascending
    with pytest.raises(WayfinderConfigError, match="ascending"):
        routing_config_from_toml(out)
