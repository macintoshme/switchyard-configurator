"""Route loading, TOML/env generation, validation, and routing logic."""

from __future__ import annotations

import graphlib
import math
import re
import tomllib
import tomli_w
from .validation import ValidationError
from pathlib import Path

from .constants import (
    ENV_FILE,
    SELF_PROVIDER_NAME,
    SWITCHYARD_DEFAULT_PORT,
)
from .models import ConfigState, Provider, Route


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# generate_toml uses tomli_w (which handles escaping correctly), but model
# IDs, route names, and endpoints with double quotes, backslashes, or control
# chars are almost certainly typos. These validators catch such inputs at
# form-submit time, before anything is written to disk.

# Characters that would break an f-string-emitted TOML double-quoted string.
_TOML_STRING_BANNED = re.compile(r'["\\\x00-\x1f]')

# Env var names per POSIX: letter or underscore, then letters/digits/underscore.
_ENV_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Values that are safe to emit unquoted in a docker-compose .env file.
_ENV_VALUE_SAFE = re.compile(r"[A-Za-z0-9_./:+-]+")


def validate_toml_identifier(name: str, label: str) -> None:
    """Validate *name* is safe as a TOML bare key (table name).

    TOML bare keys allow only ``A-Za-z0-9_-``. A ``.`` would create a
    nested table and ``]`` would terminate the table header, so both are
    rejected. Empty strings are reported by the caller.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValidationError(field=label, message=f"{label} '{name}' may only contain letters, digits, '_' and '-'")


def validate_toml_string_value(value: str, label: str) -> None:
    """Validate *value* is safe as a TOML string value.

    Rejects double quotes, backslashes, and control characters (incl.
    newlines) — while ``tomli_w`` would escape these correctly, such
    values in model IDs or URLs are almost always typos.
    """
    if _TOML_STRING_BANNED.search(value):
        raise ValidationError(field=label, message=f"{label} may not contain double quotes, backslashes, or control characters")


def validate_env_var_name(name: str, label: str) -> None:
    """Validate *name* is a valid env var name, or empty (meaning no auth)."""
    if not name:
        return None
    if not _ENV_VAR_NAME.fullmatch(name):
        raise ValidationError(
            field=label,
            message=f"{label} '{name}' must be a valid env var name "
            "(letters, digits, or underscore; must start with a "
            "letter or underscore)",
        )


def quote_env_value(value: str) -> str:
    """Quote *value* for a docker-compose ``.env`` file, only if needed.

    Docker compose parses ``.env`` lines as ``KEY=VALUE`` with ``#`` starting
    comments and whitespace splitting the value. Values containing any of
    those, plus ``$`` (variable substitution) or quote characters, are
    wrapped in double quotes with ``\\``, ``\"``, and ``$$`` escaped.
    Safe alphanumeric values pass through unquoted to keep the file
    readable.
    """
    if not value:
        return '""'
    if _ENV_VALUE_SAFE.fullmatch(value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def validate_int_field(
    value: str,
    label: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    """Validate *value* is a valid int, or empty (meaning 'use default').

    ``minimum`` / ``maximum`` are inclusive bounds. Empty input is OK —
    the numeric fields are optional and ``generate_toml`` substitutes a
    default when blank.
    """
    s = value.strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        raise ValidationError(field=label, message=f"{label} must be a whole number (got '{s}')")
    if minimum is not None and n < minimum:
        raise ValidationError(field=label, message=f"{label} must be >= {minimum} (got {n})")
    if maximum is not None and n > maximum:
        raise ValidationError(field=label, message=f"{label} must be <= {maximum} (got {n})")
    return None


def validate_float_field(
    value: str,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    """Validate *value* is a finite float, or empty (meaning 'use default')."""
    s = value.strip()
    if not s:
        return None
    try:
        x = float(s)
    except ValueError:
        raise ValidationError(field=label, message=f"{label} must be a number (got '{s}')")
    if not math.isfinite(x):
        raise ValidationError(field=label, message=f"{label} must be a finite number (got '{s}')")
    if minimum is not None and x < minimum:
        raise ValidationError(field=label, message=f"{label} must be >= {minimum} (got {x})")
    if maximum is not None and x > maximum:
        raise ValidationError(field=label, message=f"{label} must be <= {maximum} (got {x})")
    return None


def validate_weights(
    value: str, label: str, expected_count: int | None = None
) -> None:
    """Validate a comma-separated list of non-negative floats.

    When *expected_count* is given (e.g., the number of targets in a
    random route), the count of weights must match — switchyard rejects
    a mismatched list at startup.
    """
    s = value.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return None
    weights: list[float] = []
    for p in parts:
        try:
            w = float(p)
        except ValueError:
            raise ValidationError(field=label, message=f"{label} entry '{p}' is not a number")
        if not math.isfinite(w):
            raise ValidationError(field=label, message=f"{label} entry '{p}' must be finite")
        if w < 0:
            raise ValidationError(field=label, message=f"{label} entry '{p}' must be >= 0")
        weights.append(w)
    if expected_count is not None and len(weights) != expected_count:
        raise ValidationError(
            field=label,
            message=f"{label} has {len(weights)} value(s) but the route has "
            f"{expected_count} target(s) — counts must match",
        )


# ---------------------------------------------------------------------------
# TOML loading
# ---------------------------------------------------------------------------

def load_existing_routes(
    path: Path,
) -> tuple[list[Route], list[Provider], dict[str, dict], str | None]:
    """Parse an existing routes.toml file into routes + providers + extras.

    Returns ``(routes, providers, model_extras, error)``. On a TOML parse
    error, returns ``([], [], {}, error_message)`` so the caller can surface
    the problem instead of silently treating a corrupt file as empty. A
    missing file is not an error — returns ``([], [], {}, None)``.
    """
    if not path.exists():
        return [], [], {}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [], [], {}, f"{path.name}: {e}"
    return parse_routes_text(text, source=path.name)


def parse_routes_text(
    text: str, source: str = "routes.toml"
) -> tuple[list[Route], list[Provider], dict[str, dict], str | None]:
    """Parse routes.toml content into routes + providers + extras.

    Text-based twin of :func:`load_existing_routes` for sources that are not
    files (e.g. a Kubernetes ConfigMap). Each ``[llm_clients.*]`` block
    becomes a Provider. Models are pulled from targets and assigned to the
    provider referenced by each target's ``llm_client`` field, so the user
    can edit routes without re-fetching. Each target's ``extra_body`` (if
    any) is collected into a ``model id -> dict`` map.

    On a TOML parse error, returns ``([], [], {}, error_message)``.
    """
    routes: list[Route] = []
    providers: list[Provider] = []
    model_extras: dict[str, dict] = {}
    provider_by_name: dict[str, Provider] = {}

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return routes, providers, model_extras, f"{source}: {e}"

    # LLM clients — one Provider per [llm_clients.*] block
    clients = data.get("llm_clients", {})
    for client_name, client_cfg in clients.items():
        provider = Provider(
            name=client_name,
            endpoint=client_cfg.get("base_url", ""),
            api_key_env=client_cfg.get("api_key_env", ""),
            max_retries=client_cfg.get("max_retries", 2),
        )
        providers.append(provider)
        provider_by_name[client_name] = provider

    # Targets — collect model IDs and assign to the provider each target uses
    targets = data.get("targets", {})
    target_to_model: dict[str, str] = {}
    for tname, tcfg in targets.items():
        mid = tcfg.get("id", "")
        client = tcfg.get("llm_client", "")
        if mid:
            target_to_model[tname] = mid
            if client and client in provider_by_name:
                p = provider_by_name[client]
                if mid not in p.selected_models:
                    p.selected_models.append(mid)
                if mid not in p.available_models:
                    p.available_models.append(mid)
        extra = tcfg.get("extra_body")
        if mid and isinstance(extra, dict) and extra:
            model_extras[mid] = extra

    # Routes
    raw_routes = data.get("routes", {})
    for rname, rcfg in raw_routes.items():
        rid = rcfg.get("id", rname)
        rtype = rcfg.get("type", "passthrough")
        rmode = rcfg.get("mode", "")

        if rtype == "llm_classifier":
            if rmode == "escalation":
                internal_type = "llm_classifier_escalation"
            elif rmode == "custom":
                internal_type = "llm_classifier_custom"
            else:
                internal_type = "llm_classifier_capability"
        else:
            internal_type = rtype

        route = Route(name=rname, id=rid, type=internal_type)

        def _resolve(key: str) -> str:
            """Resolve a target-name reference to its model id (if known)."""
            ref = rcfg.get(key, "")
            if not isinstance(ref, str):
                return ""
            return target_to_model.get(ref, ref)

        def _str_field(field: str, key: str) -> None:
            """Copy a scalar TOML value into the route field as a string."""
            if key in rcfg:
                setattr(route, field, str(rcfg[key]))

        # passthrough
        if "target" in rcfg:
            route.target = _resolve("target")

        # random (targets list also used by llm_classifier custom mode)
        if "targets" in rcfg and isinstance(rcfg["targets"], list):
            route.targets = [_resolve_target(t, target_to_model) for t in rcfg["targets"]]
            for m in route.targets:
                _ensure_model_in_providers(m, providers)
        if "weights" in rcfg:
            w = rcfg["weights"]
            if isinstance(w, list):
                route.weights = ", ".join(str(x) for x in w)
            else:
                route.weights = str(w)
        if "seed" in rcfg:
            route.seed = str(rcfg["seed"])

        # llm_classifier shared fields (capability, escalation, custom)
        if "classifier_target" in rcfg:
            route.classifier_target = _resolve("classifier_target")
        if "weak_target" in rcfg:
            route.weak_target = _resolve("weak_target")
        if "strong_target" in rcfg:
            route.strong_target = _resolve("strong_target")
        if "default_target" in rcfg:
            route.default_target = _resolve("default_target")
        _str_field("base_threshold", "base_threshold")
        _str_field("threshold_step", "threshold_step")
        _str_field("classify_trigger", "classify_trigger")
        if "message_hash_fallback" in rcfg:
            route.message_hash_fallback = str(rcfg["message_hash_fallback"]).lower()
        if "prompt" in rcfg:
            route.prompt = str(rcfg["prompt"])
        if "response_format_type" in rcfg:
            route.response_format_type = str(rcfg["response_format_type"])
        _str_field("max_output_tokens", "max_output_tokens")
        if "response_schema" in rcfg:
            route.response_schema = str(rcfg["response_schema"])
        if "policy" in rcfg and isinstance(rcfg["policy"], dict):
            pol = rcfg["policy"]
            route.policy_type = str(pol.get("type", "target_selector"))
            route.policy_selector = str(pol.get("selector", ""))

        # capability: top-level recent_turn_window (escalation reads it from
        # the escalation block below).
        if internal_type == "llm_classifier_capability" and "recent_turn_window" in rcfg:
            route.recent_turn_window = str(rcfg["recent_turn_window"])

        # escalation-specific
        if "escalation" in rcfg and isinstance(rcfg["escalation"], dict):
            esc = rcfg["escalation"]
            if "confirmations" in esc:
                route.confirmations = str(esc["confirmations"])
            if "recent_turn_window" in esc:
                route.recent_turn_window = str(esc["recent_turn_window"])
            if "window_message_chars" in esc:
                route.window_message_chars = str(esc["window_message_chars"])

        # stage_router (and composite's stage sub-table)
        stage_cfg = rcfg
        if internal_type == "composite" and isinstance(rcfg.get("stage"), dict):
            stage_cfg = rcfg["stage"]
        if "capable_target" in stage_cfg:
            route.capable_target = target_to_model.get(
                stage_cfg["capable_target"], stage_cfg["capable_target"]
            )
        if "efficient_target" in stage_cfg:
            route.efficient_target = target_to_model.get(
                stage_cfg["efficient_target"], stage_cfg["efficient_target"]
            )
        if "picker" in stage_cfg:
            route.picker = str(stage_cfg["picker"])
        if "confidence_threshold" in stage_cfg:
            route.confidence_threshold = str(stage_cfg["confidence_threshold"])
        if "recent_turn_window" in stage_cfg:
            route.recent_turn_window = str(stage_cfg["recent_turn_window"])
        if "capable_system_prompt" in stage_cfg:
            route.capable_system_prompt = str(stage_cfg["capable_system_prompt"])
        if "efficient_system_prompt" in stage_cfg:
            route.efficient_system_prompt = str(stage_cfg["efficient_system_prompt"])
        if "handoff_notes" in stage_cfg and isinstance(stage_cfg["handoff_notes"], dict):
            hn = stage_cfg["handoff_notes"]
            if "escalation_note" in hn:
                route.handoff_escalation_note = str(hn["escalation_note"])
            if "deescalation_note" in hn:
                route.handoff_deescalation_note = str(hn["deescalation_note"])
            if "only_on_wrong_signal_escalation" in hn:
                route.handoff_only_on_wrong_signal_escalation = str(
                    hn["only_on_wrong_signal_escalation"]
                ).lower()

        # stage_router / composite classifier fallback block
        cb = None
        if internal_type == "composite":
            cb = rcfg.get("classifier") if isinstance(rcfg.get("classifier"), dict) else None
        elif isinstance(stage_cfg.get("classifier"), dict):
            cb = stage_cfg["classifier"]
        if isinstance(cb, dict):
            route.stage_classifier_enabled = "true"
            if "target" in cb:
                route.stage_classifier_target = target_to_model.get(
                    cb["target"], cb["target"]
                )
            _str_field_into(route, "stage_classifier_base_threshold",
                            cb.get("base_threshold"), "0.5")
            _str_field_into(route, "stage_classifier_threshold_step",
                            cb.get("threshold_step"), "0.1")
            _str_field_into(route, "stage_classifier_recent_turn_window",
                            cb.get("recent_turn_window"), "3")
            if "prompt" in cb:
                route.stage_classifier_prompt = str(cb["prompt"])
            if "response_format_type" in cb:
                route.stage_classifier_response_format_type = str(
                    cb["response_format_type"]
                )
            # composite classifier also carries classify_trigger /
            # message_hash_fallback inside its block (stage_router's
            # classifier block does not), so load them unconditionally
            # when present.
            if "classify_trigger" in cb:
                route.classify_trigger = str(cb["classify_trigger"])
            if "message_hash_fallback" in cb:
                route.message_hash_fallback = str(cb["message_hash_fallback"]).lower()

        # advisor
        if "executor_target" in rcfg:
            route.executor_target = _resolve("executor_target")
        if "advisor_target" in rcfg:
            route.advisor_target = _resolve("advisor_target")
        _str_field("max_reviews", "max_reviews")
        _str_field("gate_stall_turns", "gate_stall_turns")
        _str_field("gate_trigger", "gate_trigger")
        _str_field("gate_trigger_pattern", "gate_trigger_pattern")
        _str_field("gate_min_tool_results", "gate_min_tool_results")
        _str_field("advisor_max_tokens", "advisor_max_tokens")
        _str_field("advisor_temperature", "advisor_temperature")
        _str_field("transcript_max_chars", "transcript_max_chars")
        if "fail_open" in rcfg:
            route.fail_open = str(rcfg["fail_open"]).lower()
        if "reviewer_system_prompt" in rcfg:
            route.reviewer_system_prompt = str(rcfg["reviewer_system_prompt"])
        if "redo_feedback_prefix" in rcfg:
            route.redo_feedback_prefix = str(rcfg["redo_feedback_prefix"])

        # Also ensure classifier/stage/advisor model refs are in a provider list
        for m in route.model_refs():
            _ensure_model_in_providers(m, providers)

        routes.append(route)

    return routes, providers, model_extras, None


def _ensure_model_in_providers(model_id: str, providers: list[Provider]) -> None:
    """Add *model_id* to the first provider that doesn't have it yet.

    Used when loading routes that reference models not tied to a specific
    target's llm_client. Falls back to the first provider.
    """
    if not providers:
        return
    for p in providers:
        if model_id in p.available_models:
            if model_id not in p.selected_models:
                p.selected_models.append(model_id)
            return
    first = providers[0]
    if model_id not in first.available_models:
        first.available_models.append(model_id)
    if model_id not in first.selected_models:
        first.selected_models.append(model_id)


def _resolve_target(ref: object, mapping: dict[str, str]) -> str:
    """Resolve a target-name reference to its model id.

    *ref* is usually a string target name; non-string or empty values resolve
    to an empty string. If the name is in *mapping* (target -> model id), the
    model id is returned; otherwise the raw name is returned unchanged.
    """
    if not isinstance(ref, str) or not ref:
        return ""
    return mapping.get(ref, ref)


def _str_field_into(route: Route, field: str, value: object, default: str) -> None:
    """Set *route.field* from *value* as a string, falling back to *default*.

    Used when loading nested sub-table fields (e.g. the stage_router
    ``[classifier]`` block) where a missing key should keep the route's
    default rather than blanking it.
    """
    if value is None:
        setattr(route, field, default)
    else:
        setattr(route, field, str(value))


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def _parse_bool(value: str, default: bool) -> bool:
    """Parse a form-string bool ("true"/"false"/"1"/"0"/...) to Python bool.

    Empty strings fall back to *default*. Used both at TOML emit time and
    when reading values back from a dict (route_from_dict).
    """
    if not value or not value.strip():
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def _stage_classifier_block(route: Route) -> dict | None:
    """Build the ``[routes.X.classifier]`` sub-table shared by stage_router
    and composite routes. Returns ``None`` when the block should be omitted.

    For a standalone stage_router the block is emitted only when
    ``stage_classifier_enabled`` is true. For composite the block is always
    present (the classifier is the point of the route), so the caller passes
    a route whose ``stage_classifier_enabled`` is forced true.
    """
    if not _parse_bool(route.stage_classifier_enabled, False):
        return None
    block: dict[str, object] = {
        "target": route.stage_classifier_target,
        "base_threshold": float(route.stage_classifier_base_threshold or "0.5"),
    }
    step = route.stage_classifier_threshold_step.strip()
    if step:
        block["threshold_step"] = float(step)
    window = route.stage_classifier_recent_turn_window.strip()
    if window:
        block["recent_turn_window"] = int(window)
    if route.stage_classifier_prompt.strip():
        block["prompt"] = route.stage_classifier_prompt
    rft = route.stage_classifier_response_format_type.strip()
    if rft and rft != "json_schema":
        block["response_format_type"] = rft
    return block


def _handoff_notes_block(route: Route) -> dict | None:
    """Build the ``[routes.X.handoff_notes]`` sub-table, or None to omit."""
    has_note = (
        route.handoff_escalation_note.strip()
        or route.handoff_deescalation_note.strip()
    )
    if not has_note:
        return None
    block: dict[str, object] = {}
    if route.handoff_escalation_note.strip():
        block["escalation_note"] = route.handoff_escalation_note
    if route.handoff_deescalation_note.strip():
        block["deescalation_note"] = route.handoff_deescalation_note
    only = route.handoff_only_on_wrong_signal_escalation.strip()
    if only and not _parse_bool(only, True):
        # Default is true; only emit when explicitly false.
        block["only_on_wrong_signal_escalation"] = False
    return block


def generate_toml(s: ConfigState) -> str:
    """Generate routes.toml content from config state.

    Builds a dict and uses ``tomli_w`` to serialize, so string escaping
    is handled correctly by the library rather than f-string interpolation.

    Two-pass: first collect all model IDs and assign target names, then
    build target tables, then route tables. This keeps target tables from
    being interleaved inside route tables (which would break TOML parsing).
    """
    doc: dict[str, object] = {"schema_version": 1}

    # ---- llm_clients (one sub-table per provider) ----
    clients: dict[str, dict] = {}
    for provider in s.providers:
        entry: dict[str, object] = {
            "format": "openai_chat",
            "base_url": provider.endpoint,
            "max_retries": provider.max_retries,
        }
        if provider.api_key_env:
            entry["api_key_env"] = provider.api_key_env
        clients[provider.name] = entry
    if clients:
        doc["llm_clients"] = clients

    model_to_provider = s.model_to_provider()
    default_provider = s.providers[0].name if s.providers else ""

    # ---- Pass 1: collect model IDs in route order, assign target names ----
    target_map: dict[str, str] = {}  # model_id -> target_name
    target_order: list[str] = []  # preserve first-seen order

    def assign_target(model_id: str) -> None:
        if model_id and model_id not in target_map:
            target_map[model_id] = f"target_{len(target_order)}"
            target_order.append(model_id)

    for route in s.routes:
        if not route.name or not route.id:
            continue
        for m in route.model_refs():
            assign_target(m)

    # ---- targets (all grouped together) ----
    targets: dict[str, dict] = {}
    for model_id in target_order:
        tname = target_map[model_id]
        client = model_to_provider.get(model_id, default_provider)
        entry: dict[str, object] = {"id": model_id, "llm_client": client}
        # Per-model extra_body (capability flags, provider-specific request
        # params) — emitted as a sub-table; the server merges it into every
        # request body sent to this model's provider.
        extra = s.model_extras.get(model_id)
        if extra:
            entry["extra_body"] = extra
        targets[tname] = entry
    if targets:
        doc["targets"] = targets

    # Helper for the route pass: look up a target name.
    def tn(model_id: str) -> str:
        return target_map.get(model_id, "")

# ---- Pass 2: build route tables ----
    routes: dict[str, dict] = {}
    for route in s.routes:
        if not route.name or not route.id:
            continue
        # Try to use a registered handler first.
        from .route_registry import ROUTE_REGISTRY
        handler = ROUTE_REGISTRY.get(route.type)
        if handler:
            r = handler.to_toml(route, tn)
        else:
            # Fallback to the original logic for historic route types.
            r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
            mode = route.toml_mode()
            if mode:
                r["mode"] = mode

            if route.type == "passthrough":
                if route.target:
                    r["target"] = tn(route.target)

            elif route.type == "random":
                if len(route.targets) >= 2:
                    r["targets"] = [tn(m) for m in route.targets]
                    if route.weights.strip():
                        r["weights"] = [
                            float(w.strip())
                            for w in route.weights.split(",")
                            if w.strip()
                        ]
                    seed_val = route.seed.strip() or "42"
                    r["seed"] = int(seed_val)

            elif route.type == "llm_classifier_capability":
                if route.classifier_target:
                    r["classifier_target"] = tn(route.classifier_target)
                if route.weak_target:
                    r["weak_target"] = tn(route.weak_target)
                if route.strong_target:
                    r["strong_target"] = tn(route.strong_target)
                if route.base_threshold.strip():
                    r["base_threshold"] = float(route.base_threshold)
                if route.threshold_step.strip():
                    r["threshold_step"] = float(route.threshold_step)
                if route.classify_trigger.strip() and route.classify_trigger != "every_request":
                    r["classify_trigger"] = route.classify_trigger
                if _parse_bool(route.message_hash_fallback, False):
                    r["message_hash_fallback"] = True
                if route.recent_turn_window.strip():
                    r["recent_turn_window"] = int(route.recent_turn_window)
                if route.prompt.strip():
                    r["prompt"] = route.prompt
                if route.response_format_type.strip() and route.response_format_type != "json_schema":
                    r["response_format_type"] = route.response_format_type
                if route.max_output_tokens.strip():
                    r["max_output_tokens"] = int(route.max_output_tokens)

            elif route.type == "llm_classifier_escalation":
                if route.classifier_target:
                    r["classifier_target"] = tn(route.classifier_target)
                if route.weak_target:
                    r["weak_target"] = tn(route.weak_target)
                if route.strong_target:
                    r["strong_target"] = tn(route.strong_target)
                if route.prompt.strip():
                    r["prompt"] = route.prompt
                if route.response_format_type.strip() and route.response_format_type != "json_schema":
                    r["response_format_type"] = route.response_format_type
                if route.max_output_tokens.strip():
                    r["max_output_tokens"] = int(route.max_output_tokens)
                confirmations = (int(route.confirmations) if route.confirmations.strip() else 2)
                window = (int(route.recent_turn_window) if route.recent_turn_window.strip() else 28)
                msg_chars = (int(route.window_message_chars) if route.window_message_chars.strip() else 500)
                r["escalation"] = {"confirmations": confirmations, "recent_turn_window": window, "window_message_chars": msg_chars}

            elif route.type == "llm_classifier_custom":
                if route.classifier_target:
                    r["classifier_target"] = tn(route.classifier_target)
                if len(route.targets) >= 2:
                    r["targets"] = [tn(m) for m in route.targets]
                if route.default_target:
                    r["default_target"] = tn(route.default_target)
                if route.prompt.strip():
                    r["prompt"] = route.prompt
                if route.response_schema.strip():
                    r["response_schema"] = route.response_schema
                if route.response_format_type.strip() and route.response_format_type != "json_schema":
                    r["response_format_type"] = route.response_format_type
                if route.classify_trigger.strip() and route.classify_trigger != "every_request":
                    r["classify_trigger"] = route.classify_trigger
                if route.max_output_tokens.strip():
                    r["max_output_tokens"] = int(route.max_output_tokens)
                if route.policy_selector.strip():
                    r["policy"] = {"type": route.policy_type or "target_selector", "selector": route.policy_selector}

            elif route.type == "stage_router":
                if route.capable_target:
                    r["capable_target"] = tn(route.capable_target)
                if route.efficient_target:
                    r["efficient_target"] = tn(route.efficient_target)
                r["picker"] = route.picker
                if route.confidence_threshold.strip():
                    r["confidence_threshold"] = float(route.confidence_threshold)
                stw = route.recent_turn_window.strip()
                if stw:
                    r["recent_turn_window"] = int(stw)
                if route.capable_system_prompt.strip():
                    r["capable_system_prompt"] = route.capable_system_prompt
                if route.efficient_system_prompt.strip():
                    r["efficient_system_prompt"] = route.efficient_system_prompt
                hb = _handoff_notes_block(route)
                if hb is not None:
                    r["handoff_notes"] = hb
                cb = _stage_classifier_block(route)
                if cb is not None:
                    r["classifier"] = cb

            elif route.type == "advisor":
                if route.executor_target:
                    r["executor_target"] = tn(route.executor_target)
                if route.advisor_target:
                    r["advisor_target"] = tn(route.advisor_target)
                r["max_reviews"] = int(route.max_reviews) if route.max_reviews.strip() else 3
                r["gate_stall_turns"] = (int(route.gate_stall_turns) if route.gate_stall_turns.strip() else 30)
                if route.gate_trigger.strip() and route.gate_trigger != "no_tool_call":
                    r["gate_trigger"] = route.gate_trigger
                if route.gate_trigger_pattern.strip():
                    r["gate_trigger_pattern"] = route.gate_trigger_pattern
                if route.gate_min_tool_results.strip():
                    r["gate_min_tool_results"] = int(route.gate_min_tool_results)
                if route.advisor_max_tokens.strip():
                    r["advisor_max_tokens"] = int(route.advisor_max_tokens)
                if route.advisor_temperature.strip():
                    try:
                        r["advisor_temperature"] = float(route.advisor_temperature)
                    except ValueError:
                        pass
                if route.transcript_max_chars.strip():
                    r["transcript_max_chars"] = int(route.transcript_max_chars)
                if not _parse_bool(route.fail_open, True):
                    r["fail_open"] = False
                if route.reviewer_system_prompt.strip():
                    r["reviewer_system_prompt"] = route.reviewer_system_prompt
                if route.redo_feedback_prefix.strip():
                    r["redo_feedback_prefix"] = route.redo_feedback_prefix

            elif route.type == "composite":
                stage_block: dict[str, object] = {}
                if route.capable_target:
                    stage_block["capable_target"] = tn(route.capable_target)
                if route.efficient_target:
                    stage_block["efficient_target"] = tn(route.efficient_target)
                if route.confidence_threshold.strip():
                    stage_block["confidence_threshold"] = float(route.confidence_threshold)
                stw = route.recent_turn_window.strip()
                if stw:
                    stage_block["recent_turn_window"] = int(stw)
                if route.capable_system_prompt.strip():
                    stage_block["capable_system_prompt"] = route.capable_system_prompt
                if route.efficient_system_prompt.strip():
                    stage_block["efficient_system_prompt"] = route.efficient_system_prompt
                hb = _handoff_notes_block(route)
                if hb is not None:
                    stage_block["handoff_notes"] = hb
                if stage_block:
                    r["stage"] = stage_block
                comp_route = Route(
                    stage_classifier_enabled="true",
                    stage_classifier_target=route.stage_classifier_target or "",
                    stage_classifier_base_threshold=route.stage_classifier_base_threshold or "0.5",
                    stage_classifier_threshold_step=route.stage_classifier_threshold_step or "0.1",
                    stage_classifier_recent_turn_window=route.stage_classifier_recent_turn_window or "3",
                    stage_classifier_prompt=route.stage_classifier_prompt or "",
                    stage_classifier_response_format_type=route.stage_classifier_response_format_type or "json_schema",
                )
                cb = _stage_classifier_block(comp_route)
                if cb is not None:
                    if route.classify_trigger.strip() and route.classify_trigger != "every_request":
                        cb["classify_trigger"] = route.classify_trigger
                    if _parse_bool(route.message_hash_fallback, False):
                        cb["message_hash_fallback"] = True
                    r["classifier"] = cb
        routes[route.name] = r
    if routes:
        doc["routes"] = routes


    header = "# Generated by configure.py - NeMo Switchyard route configuration\n"
    return header + tomli_w.dumps(doc)


def update_env_file(providers: list[Provider]) -> str:
    """Read existing .env, update all provider API keys, return new content."""
    keys_to_set: dict[str, str] = {}
    for p in providers:
        if p.api_key_env and p.api_key:
            keys_to_set[p.api_key_env] = p.api_key

    lines: list[str] = []
    seen: set[str] = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            matched = False
            for env_name, value in keys_to_set.items():
                if line.startswith(f"{env_name}="):
                    lines.append(f"{env_name}={quote_env_value(value)}")
                    seen.add(env_name)
                    matched = True
                    break
            if not matched:
                lines.append(line)

    for env_name, value in keys_to_set.items():
        if env_name not in seen:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"# {env_name} (set by configure.py)")
            lines.append(f"{env_name}={quote_env_value(value)}")

    return "\n".join(lines) + "\n"


def read_env_var_from(path: Path, var_name: str) -> str:
    """Read a variable from a specific env file (not shell environment)."""
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{var_name}="):
            return line[len(var_name) + 1:].strip()
    return ""


# ---------------------------------------------------------------------------
# Self-referencing provider (route chaining)
# ---------------------------------------------------------------------------

def _switchyard_base_url() -> str:
    """Return the switchyard /v1 base URL used for self-referencing calls."""
    port = read_env_var_from(ENV_FILE, "SWITCHYARD_PORT") or SWITCHYARD_DEFAULT_PORT
    return f"http://localhost:{port}/v1"


def sync_self_provider(state: ConfigState) -> None:
    """Maintain the synthetic 'self' provider whose models are route ids.

    The self provider lets one route's target point at another route's id,
    enabling cascading escalation across more than two tiers. Its model
    list is derived from route ids that are not already served by another
    provider — a passthrough route whose id matches an upstream model id
    (e.g. ``GLM-5.2``) is served by that upstream provider directly, so it
    does not need a self-reference. A route with a synthetic id (e.g.
    ``switchyard/good``) is only reachable by calling switchyard itself,
    so it belongs in the self provider. This is called whenever routes
    change so the self provider's model list stays current.
    """
    # Collect all model ids served by non-self providers.
    upstream_model_ids: set[str] = set()
    for p in state.providers:
        if p.name != SELF_PROVIDER_NAME:
            upstream_model_ids.update(p.selected_models)

    # A route id belongs in the self provider when it is not also an
    # upstream model id — calling it requires routing through switchyard.
    route_ids = [
        r.id for r in state.routes
        if r.id and r.id not in upstream_model_ids
    ]
    self_provider: Provider | None = None
    for p in state.providers:
        if p.name == SELF_PROVIDER_NAME:
            self_provider = p
            break
    # Remove the self provider when there are no routes to chain to.
    if not route_ids:
        if self_provider is not None:
            state.providers = [
                p for p in state.providers if p.name != SELF_PROVIDER_NAME
            ]
        return
    if self_provider is None:
        self_provider = Provider(
            name=SELF_PROVIDER_NAME,
            endpoint=_switchyard_base_url(),
            api_key_env="",  # no auth on local self-calls
            api_key="",
            max_retries=0,  # avoid retry storms on cascading self-calls
        )
        state.providers.append(self_provider)
    self_provider.endpoint = _switchyard_base_url()
    self_provider.available_models = list(route_ids)
    self_provider.selected_models = list(route_ids)


def find_route_cycles(state: ConfigState) -> list[list[str]]:
    """Find cycles in the route dependency graph.

    A route depends on another route when one of its targets references a
    route id (served by the self provider). Returns a list containing at
    most one cycle (as a list of route ids where each element depends on
    the next, with the first element repeated at the end to close the loop).
    Returns an empty list if there are no cycles.

    Only the first cycle is reported — fix it and re-validate to surface
    any others.
    """
    route_ids = {r.id for r in state.routes if r.id}
    deps: dict[str, set[str]] = {rid: set() for rid in route_ids}
    for r in state.routes:
        if not r.id:
            continue
        for ref in r.model_refs():
            if ref in route_ids and ref != r.id:
                deps[r.id].add(ref)

    ts = graphlib.TopologicalSorter()
    for rid, deps_set in deps.items():
        ts.add(rid, *sorted(deps_set))
    try:
        ts.prepare()
    except graphlib.CycleError as e:
        # args[1] is the cycle as a list with the first node repeated at
        # the end. graphlib reports in reverse dependency order, so we
        # reverse to match the format where each element's successor is
        # its dependency.
        cycle = e.args[1]
        return [cycle[:0:-1] + [cycle[0]]]
    return []


# ---------------------------------------------------------------------------
# Passthrough route helpers
# ---------------------------------------------------------------------------

def auto_passthrough_name(model_id: str) -> str:
    """TOML table name of the auto-generated passthrough for *model_id*.

    The model ID is lowercased with ``/``, ``.``, ``-`` replaced by ``_``
    so it's a valid bare key.
    """
    safe = model_id.replace("/", "_").replace(".", "_").replace("-", "_").lower()
    return f"passthrough_{safe}"


def _passthrough_route_for(model_id: str) -> Route:
    """Build the auto-generated passthrough route for a model id."""
    return Route(
        name=auto_passthrough_name(model_id),
        id=model_id,
        type="passthrough",
        target=model_id,
    )


def is_auto_passthrough(route: Route) -> bool:
    """True when *route* is an auto-generated passthrough, not a custom one.

    Auto passthroughs are created by :func:`ensure_passthrough_routes` with
    a name derived from the model id and ``id == target == model id``. A
    passthrough the user created by hand keeps its own name (or has a
    different id/target pair, e.g. a synthetic route id) and counts as a
    custom route in the UI.
    """
    if route.type != "passthrough" or not route.id:
        return False
    if route.target != route.id:
        return False
    return route.name == auto_passthrough_name(route.id)


def ensure_passthrough_routes(
    routes: list[Route], selected_models: list[str]
) -> list[Route]:
    """Create passthrough routes for models that don't have one.

    Returns the new routes added (does not mutate *routes*). A model is
    considered "covered" if any existing route already uses it as an ``id``
    (i.e., it's directly accessible) — we only create passthroughs for models
    that are referenced as targets but have no route of their own.
    """
    existing_ids = {r.id for r in routes if r.id}
    new_routes: list[Route] = []
    for model_id in selected_models:
        if model_id in existing_ids:
            continue
        new_routes.append(_passthrough_route_for(model_id))
    return new_routes