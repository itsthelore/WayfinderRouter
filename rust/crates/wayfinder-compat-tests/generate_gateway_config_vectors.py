#!/usr/bin/env python3
"""Generate Python-authoritative gateway TOML compatibility vectors.

Run from the repository root:

    python3 rust/crates/wayfinder-compat-tests/generate_gateway_config_vectors.py

The deterministic JSON written to stdout is checked in as
``fixtures/gateway-config.json``.  Semantic summaries retain model/key order and
credential *references*, but redact virtual-key digests and never resolve an
environment variable or execute ``api_key_cmd``.

Python currently accepts TOML non-finite floats because comparisons with NaN
do not trip its range checks.  Those cases are recorded as Python-valid audit
evidence and explicitly annotated for the Rust parser's intentional finite-only
hardening boundary.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))

from wayfinder_router.config import WayfinderConfigError  # noqa: E402
from wayfinder_router.gateway import (  # noqa: E402
    Budget,
    GatewayConfig,
    RateLimit,
    gateway_config_from_toml,
)

WHERE = "gateway-compat-vector"
UPPER_HASH = "ABCDEF01" * 8
LOWER_HASH = "0123456789abcdef" * 4


def budget_summary(budget: Budget | None) -> dict[str, Any] | None:
    if budget is None:
        return None
    return {
        "limit": budget.limit,
        "window": budget.window,
        "on_breach": budget.on_breach,
    }


def rate_limit_summary(rate_limit: RateLimit | None) -> dict[str, Any] | None:
    if rate_limit is None:
        return None
    return {
        "rpm": rate_limit.rpm,
        "tpm": rate_limit.tpm,
        "window": rate_limit.window,
    }


def summarize(config: GatewayConfig) -> dict[str, Any]:
    """Return safe semantic JSON without resolving or exposing credentials."""
    return {
        "route_on": config.route_on,
        "sticky": config.sticky,
        "sticky_cooldown": config.sticky_cooldown,
        "slash_directives": config.slash_directives,
        "offline": config.offline,
        "retries": config.retries,
        "breaker_threshold": config.breaker_threshold,
        "breaker_cooldown": float(config.breaker_cooldown),
        "failover": config.failover,
        "budget": budget_summary(config.budget),
        "cache": (
            {
                "enabled": config.cache.enabled,
                "ttl": config.cache.ttl,
                "max_entries": config.cache.max_entries,
                "max_bytes": config.cache.max_bytes,
            }
            if config.cache is not None
            else None
        ),
        "rate_limit": rate_limit_summary(config.rate_limit),
        # Lists of entries make insertion order an explicit compatibility contract.
        "keys": [
            {
                "name": name,
                "credential_digest": {
                    "algorithm": "sha256",
                    "value": "<redacted>",
                    "length": len(key.hash),
                    "normalized_lowercase": key.hash == key.hash.lower(),
                },
                "tags": list(key.tags),
                "budget": budget_summary(key.budget),
                "rate_limit": rate_limit_summary(key.rate_limit),
                "models": list(key.models),
            }
            for name, key in config.keys.items()
        ],
        "models": [
            {
                "name": name,
                "provider": model.provider.value,
                "base_url": model.base_url,
                "model": model.model,
                "tier": model.tier.value if model.tier is not None else None,
                # These are inert config references, never resolved values.
                "credential_reference": {
                    "api_key_env": model.api_key_env,
                    "api_key_cmd": model.api_key_cmd,
                },
                "cost_per_1k": model.cost_per_1k,
                "fallbacks": list(model.fallbacks),
                "context_window": model.context_window,
            }
            for name, model in config.models.items()
        ],
    }


def json_safe(value: Any) -> Any:
    """Represent non-finite audit values without emitting non-standard JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return {"nonfinite": "nan"}
        return {"nonfinite": "positive_infinity" if value > 0 else "negative_infinity"}
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def generate_case(specification: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": specification["name"],
        "compatibility": specification.get("compatibility", "exact"),
        "toml": specification["toml"],
    }
    try:
        config = gateway_config_from_toml(specification["toml"], where=WHERE)
    except WayfinderConfigError as error:
        result["outcome"] = {
            "status": "invalid",
            "python_error": str(error),
        }
    else:
        result["outcome"] = {
            "status": "valid",
            "summary": json_safe(summarize(config)),
        }
    return result


FULL_SCHEMA = f'''[gateway]
route_on = "user"
sticky = true
sticky_cooldown = 3
slash_directives = true
offline = true
retries = 4
breaker_threshold = 7
breaker_cooldown = 12.5
failover = "escalate"
future_flag = "ignored"

[gateway.budget]
limit = 25
window = "month"
on_breach = "block"
future_budget_field = true

[gateway.cache]
enabled = true
ttl = 600
max_entries = 2048
max_bytes = 134217728

[gateway.rate_limit]
rpm = 60
tpm = 100000
window = 30

[gateway.keys."team z"]
hash = "{UPPER_HASH}"
tags = ["production", "z-first"]
models = ["local", "cloud"]

[gateway.keys."team z".budget]
limit = 2.5
window = "all"
on_breach = "block"

[gateway.keys."team z".rate_limit]
rpm = 5
window = 10

[gateway.keys.alpha]
hash = "{LOWER_HASH}"
tags = []
models = []

[gateway.keys.alpha.rate_limit]
tpm = 5000

[gateway.models.local]
base_url = "http://127.0.0.1:11434/v1"
model = "qwen-local"
cost_per_1k = 0
fallbacks = []
context_window = 8192

[gateway.models.cloud]
base_url = "https://api.example.test/v1"
model = "cloud-primary"
api_key_env = "EXAMPLE_API_KEY"
api_key_cmd = "security find-generic-password -s wayfinder-cloud -w"
cost_per_1k = 1.25
fallbacks = ["cloud.backup"]
context_window = 128000
future_model_field = 9

[gateway.models."cloud.backup"]
base_url = "https://backup.example.test/v1"
model = "cloud-backup"
api_key_env = "EXAMPLE_BACKUP_API_KEY"
cost_per_1k = 1.5
'''

ONE_MODEL = '''[gateway.models.local]
base_url = "http://127.0.0.1:11434/v1"
model = "local-model"
'''


CASES: tuple[dict[str, str], ...] = (
    {"name": "document_without_gateway_uses_defaults", "toml": ""},
    {"name": "empty_gateway_table_uses_defaults", "toml": "[gateway]\n"},
    {
        "name": "explicit_defaults_and_unknown_fields",
        "toml": (
            "[routing]\nthreshold = 0.4\n\n"
            "[gateway]\nroute_on = \"turn\"\nsticky = false\nsticky_cooldown = 0\n"
            "slash_directives = false\noffline = false\nretries = 2\n"
            "breaker_threshold = 5\nbreaker_cooldown = 30\n"
            "failover = \"same-tier\"\nunknown = { nested = true }\n"
        ),
    },
    {"name": "full_schema_preserves_key_and_model_order", "toml": FULL_SCHEMA},
    {
        "name": "nested_blocks_apply_defaults",
        "toml": (
            "[gateway.budget]\nlimit = 5\n\n"
            "[gateway.cache]\n\n"
            "[gateway.rate_limit]\nrpm = 10\n\n" + ONE_MODEL
        ),
    },
    {"name": "falsey_models_boolean", "toml": "[gateway]\nmodels = false\n"},
    {"name": "falsey_models_integer", "toml": "[gateway]\nmodels = 0\n"},
    {"name": "falsey_models_float", "toml": "[gateway]\nmodels = 0.0\n"},
    {"name": "falsey_models_string", "toml": "[gateway]\nmodels = \"\"\n"},
    {"name": "falsey_models_array", "toml": "[gateway]\nmodels = []\n"},
    {"name": "falsey_models_inline_table", "toml": "[gateway]\nmodels = {}\n"},
    {
        "name": "zero_cost_and_empty_optional_lists",
        "toml": (
            "[gateway.models.zero]\nbase_url = \"http://zero.test/v1\"\n"
            "model = \"zero\"\ncost_per_1k = 0.0\nfallbacks = []\n"
        ),
    },
    {
        "name": "apple_foundation_models_typed_local_provider",
        "toml": (
            "[gateway.models.apple-local]\n"
            "provider = \"apple-foundation-models\"\n"
            "model = \"system-default\"\n"
            "tier = \"local\"\n"
            "context_window = 4096\n"
        ),
    },
    {
        "name": "all_route_scope_with_tpm_only",
        "toml": (
            "[gateway]\nroute_on = \"all\"\n\n"
            "[gateway.rate_limit]\ntpm = 1\nwindow = 0.5\n\n" + ONE_MODEL
        ),
    },
    {"name": "malformed_toml", "toml": "[gateway\noffline = true\n"},
    {"name": "gateway_scalar", "toml": "gateway = 1\n"},
    {"name": "invalid_route_scope", "toml": "[gateway]\nroute_on = \"latest\"\n"},
    {"name": "route_scope_wrong_type", "toml": "[gateway]\nroute_on = false\n"},
    {"name": "sticky_wrong_type", "toml": "[gateway]\nsticky = 1\n"},
    {"name": "sticky_cooldown_negative", "toml": "[gateway]\nsticky_cooldown = -1\n"},
    {"name": "sticky_cooldown_boolean", "toml": "[gateway]\nsticky_cooldown = true\n"},
    {"name": "sticky_cooldown_float", "toml": "[gateway]\nsticky_cooldown = 1.0\n"},
    {"name": "slash_directives_wrong_type", "toml": "[gateway]\nslash_directives = \"yes\"\n"},
    {"name": "offline_wrong_type", "toml": "[gateway]\noffline = 1\n"},
    {"name": "retries_negative", "toml": "[gateway]\nretries = -1\n"},
    {"name": "retries_boolean", "toml": "[gateway]\nretries = true\n"},
    {"name": "breaker_threshold_zero", "toml": "[gateway]\nbreaker_threshold = 0\n"},
    {"name": "breaker_threshold_float", "toml": "[gateway]\nbreaker_threshold = 1.0\n"},
    {"name": "breaker_cooldown_negative", "toml": "[gateway]\nbreaker_cooldown = -0.1\n"},
    {"name": "breaker_cooldown_boolean", "toml": "[gateway]\nbreaker_cooldown = true\n"},
    {"name": "invalid_failover", "toml": "[gateway]\nfailover = \"random\"\n"},
    {"name": "budget_scalar", "toml": "[gateway]\nbudget = 5\n"},
    {"name": "budget_missing_limit", "toml": "[gateway.budget]\nwindow = \"day\"\n"},
    {"name": "budget_zero", "toml": "[gateway.budget]\nlimit = 0\n"},
    {"name": "budget_boolean", "toml": "[gateway.budget]\nlimit = true\n"},
    {"name": "budget_invalid_window", "toml": "[gateway.budget]\nlimit = 1\nwindow = \"year\"\n"},
    {"name": "budget_invalid_breach", "toml": "[gateway.budget]\nlimit = 1\non_breach = \"panic\"\n"},
    {"name": "cache_scalar", "toml": "[gateway]\ncache = 5\n"},
    {"name": "cache_enabled_wrong_type", "toml": "[gateway.cache]\nenabled = \"yes\"\n"},
    {"name": "cache_negative_ttl", "toml": "[gateway.cache]\nttl = -1\n"},
    {"name": "cache_zero_entries", "toml": "[gateway.cache]\nmax_entries = 0\n"},
    {"name": "cache_boolean_bytes", "toml": "[gateway.cache]\nmax_bytes = true\n"},
    {"name": "rate_limit_scalar", "toml": "[gateway]\nrate_limit = 5\n"},
    {"name": "rate_limit_empty", "toml": "[gateway.rate_limit]\n"},
    {"name": "rate_limit_zero_rpm", "toml": "[gateway.rate_limit]\nrpm = 0\n"},
    {"name": "rate_limit_boolean_tpm", "toml": "[gateway.rate_limit]\ntpm = true\n"},
    {"name": "rate_limit_zero_window", "toml": "[gateway.rate_limit]\nrpm = 1\nwindow = 0\n"},
    {"name": "keys_scalar", "toml": "[gateway]\nkeys = 5\n"},
    {"name": "key_entry_scalar", "toml": "[gateway.keys]\nteam = 5\n"},
    {"name": "key_missing_hash", "toml": "[gateway.keys.team]\ntags = [\"x\"]\n"},
    {"name": "key_bad_hash", "toml": "[gateway.keys.team]\nhash = \"not-a-digest\"\n"},
    {
        "name": "key_empty_tag",
        "toml": f"[gateway.keys.team]\nhash = \"{LOWER_HASH}\"\ntags = [\"ok\", \"\"]\n",
    },
    {
        "name": "key_models_wrong_type",
        "toml": f"[gateway.keys.team]\nhash = \"{LOWER_HASH}\"\nmodels = \"local\"\n",
    },
    {"name": "truthy_models_boolean", "toml": "[gateway]\nmodels = true\n"},
    {"name": "truthy_models_integer", "toml": "[gateway]\nmodels = 1\n"},
    {"name": "truthy_models_string", "toml": "[gateway]\nmodels = \"configured\"\n"},
    {"name": "truthy_models_array", "toml": "[gateway]\nmodels = [0]\n"},
    {"name": "model_entry_scalar", "toml": "[gateway.models]\nlocal = 1\n"},
    {"name": "model_missing_base_url", "toml": "[gateway.models.local]\nmodel = \"m\"\n"},
    {
        "name": "model_unknown_provider",
        "toml": ONE_MODEL + "provider = \"future-provider\"\n",
    },
    {
        "name": "apple_provider_requires_system_default",
        "toml": (
            "[gateway.models.apple-local]\nprovider = \"apple-foundation-models\"\n"
            "model = \"internal-version\"\ntier = \"local\"\n"
        ),
    },
    {
        "name": "apple_provider_requires_local_tier",
        "toml": (
            "[gateway.models.apple-local]\nprovider = \"apple-foundation-models\"\n"
            "model = \"system-default\"\n"
        ),
    },
    {
        "name": "apple_provider_rejects_url",
        "toml": (
            "[gateway.models.apple-local]\nprovider = \"apple-foundation-models\"\n"
            "base_url = \"http://localhost/v1\"\nmodel = \"system-default\"\n"
            "tier = \"local\"\n"
        ),
    },
    {
        "name": "apple_provider_rejects_credentials",
        "toml": (
            "[gateway.models.apple-local]\nprovider = \"apple-foundation-models\"\n"
            "model = \"system-default\"\ntier = \"local\"\napi_key_env = \"APPLE_KEY\"\n"
        ),
    },
    {"name": "model_empty_base_url", "toml": "[gateway.models.local]\nbase_url = \"\"\nmodel = \"m\"\n"},
    {"name": "model_missing_model", "toml": "[gateway.models.local]\nbase_url = \"http://x/v1\"\n"},
    {
        "name": "model_empty_api_key_env",
        "toml": ONE_MODEL + "api_key_env = \"\"\n",
    },
    {
        "name": "model_command_without_environment",
        "toml": ONE_MODEL + "api_key_cmd = \"security lookup\"\n",
    },
    {
        "name": "model_negative_cost",
        "toml": ONE_MODEL + "cost_per_1k = -0.01\n",
    },
    {
        "name": "model_bad_fallback_shape",
        "toml": ONE_MODEL + "fallbacks = \"backup\"\n",
    },
    {
        "name": "model_empty_fallback_name",
        "toml": ONE_MODEL + "fallbacks = [\"\"]\n",
    },
    {
        "name": "model_bad_context_window",
        "toml": ONE_MODEL + "context_window = 0\n",
    },
    {
        "name": "unknown_fallback_cross_reference",
        "toml": ONE_MODEL + "fallbacks = [\"missing\"]\n",
    },
    {
        "name": "self_fallback_cross_reference",
        "toml": ONE_MODEL + "fallbacks = [\"local\"]\n",
    },
    {
        "name": "unknown_key_model_cross_reference",
        "toml": (
            f"[gateway.keys.team]\nhash = \"{LOWER_HASH}\"\nmodels = [\"missing\"]\n\n"
            + ONE_MODEL
        ),
    },
    {
        "name": "nonfinite_breaker_cooldown_is_rust_hardening_boundary",
        "compatibility": "rust_rejects_nonfinite",
        "toml": "[gateway]\nbreaker_cooldown = nan\n",
    },
    {
        "name": "nonfinite_budget_is_rust_hardening_boundary",
        "compatibility": "rust_rejects_nonfinite",
        "toml": "[gateway.budget]\nlimit = nan\n",
    },
    {
        "name": "nonfinite_cache_ttl_is_rust_hardening_boundary",
        "compatibility": "rust_rejects_nonfinite",
        "toml": "[gateway.cache]\nttl = nan\n",
    },
    {
        "name": "nonfinite_rate_window_is_rust_hardening_boundary",
        "compatibility": "rust_rejects_nonfinite",
        "toml": "[gateway.rate_limit]\nrpm = 1\nwindow = inf\n",
    },
    {
        "name": "nonfinite_model_cost_is_rust_hardening_boundary",
        "compatibility": "rust_rejects_nonfinite",
        "toml": ONE_MODEL + "cost_per_1k = nan\n",
    },
)


print(
    json.dumps(
        [generate_case(case) for case in CASES],
        ensure_ascii=False,
        allow_nan=False,
        indent=1,
    )
)
