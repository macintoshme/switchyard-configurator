"""Stateful engine for the Switchyard web configurator.

Reuses the same underlying logic from the ``switchyard_config`` package but without the ``textual`` dependency, so it can run headless inside a small web API.

All mutations are serialized behind a lock; synchronous FastAPI ``def``
endpoints run each call in a threadpool, so a long network probe won't
block other requests.
"""

from __future__ import annotations

# Applied from the same shell fallback chain as the TUI.
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from switchyard_config import constants as C
from switchyard_config import hints as model_hints
from switchyard_config import kube
from switchyard_config.endpoints import (
    fetch_models_from_endpoint,
    is_switchyard_running,
    probe_endpoint,
    restart_switchyard,
)
from switchyard_config.files import clear_draft, rotate_backups, write_draft
from switchyard_config.models import (
    ROUTE_TYPE_KEYS,
    ROUTE_TYPES,
    ConfigState,
    Provider,
    Route,
)
from switchyard_config.providers import (
    apply_provider_meta,
    apply_provider_meta_dict,
    load_provider_meta,
    provider_display_name,
    provider_meta_json,
    save_provider_meta,
)
from switchyard_config.routes import (
    ensure_passthrough_routes,
    find_route_cycles,
    generate_toml,
    load_existing_routes,
    parse_routes_text,
    read_env_var_from,
    sync_self_provider,
    update_env_file,
    validate_env_var_name,
    validate_float_field,
    validate_int_field,
    validate_toml_identifier,
    validate_toml_string_value,
    validate_weights,
)


from switchyard_config.validation import ValidationError

def _run_validator(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except ValidationError as e:
        return e.message
    return None

def display_path(p: str) -> str:
    """Show container paths as host-relative (the container mounts the repo at /app)."""
    if p.startswith("/app/"):
        return "./" + p[len("/app/"):]
    return p


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def route_type_label(route_type: str) -> str:
    """Human-readable label for a route type key."""
    for key, (label, _, _) in zip(ROUTE_TYPE_KEYS, ROUTE_TYPES):
        if key == route_type:
            return label
    return route_type


def route_targets_summary(r: Route) -> str:
    """Compact one-line summary of a route's model targets."""
    if r.type == "passthrough":
        return r.target or "(none)"
    if r.type == "random":
        weights = f"  w={r.weights}" if r.weights.strip() else ""
        return ", ".join(r.targets) + weights if r.targets else "(none)"
    if r.type == "llm_classifier_capability":
        return f"cls={r.classifier_target}, weak={r.weak_target}, strong={r.strong_target}"
    if r.type == "llm_classifier_escalation":
        return (
            f"judge={r.classifier_target}, weak={r.weak_target}, "
            f"strong={r.strong_target}, conf={r.confirmations}"
        )
    if r.type == "llm_classifier_custom":
        n = len(r.targets)
        return f"cls={r.classifier_target}, {n} target(s), default={r.default_target}"
    if r.type == "stage_router":
        return f"cap={r.capable_target}, eff={r.efficient_target}"
    if r.type == "advisor":
        return f"exec={r.executor_target}, adv={r.advisor_target}"
    if r.type == "composite":
        return (
            f"judge={r.stage_classifier_target}, cap={r.capable_target}, "
            f"eff={r.efficient_target}"
        )
    return "(unknown)"


def provider_to_dict(p: Provider) -> dict:
    return {
        "name": p.name,
        "display_name": p.display_name,
        "endpoint": p.endpoint,
        "api_key_env": p.api_key_env,
        "api_key": p.api_key,
        "max_retries": p.max_retries,
        "available_models": list(p.available_models),
        "selected_models": list(p.selected_models),
        "is_self": p.name == C.SELF_PROVIDER_NAME,
        "display_label": provider_display_name(p),
    }


def route_to_dict(r: Route) -> dict:
    return {
        "name": r.name,
        "id": r.id,
        "type": r.type,
        "target": r.target,
        "targets": list(r.targets),
        "weights": r.weights,
        "seed": r.seed,
        "classifier_target": r.classifier_target,
        "weak_target": r.weak_target,
        "strong_target": r.strong_target,
        "base_threshold": r.base_threshold,
        "threshold_step": r.threshold_step,
        "classify_trigger": r.classify_trigger,
        "message_hash_fallback": r.message_hash_fallback,
        "prompt": r.prompt,
        "response_format_type": r.response_format_type,
        "max_output_tokens": r.max_output_tokens,
        "recent_turn_window": r.recent_turn_window,
        "confirmations": r.confirmations,
        "window_message_chars": r.window_message_chars,
        "default_target": r.default_target,
        "response_schema": r.response_schema,
        "policy_type": r.policy_type,
        "policy_selector": r.policy_selector,
        "capable_target": r.capable_target,
        "efficient_target": r.efficient_target,
        "picker": r.picker,
        "confidence_threshold": r.confidence_threshold,
        "capable_system_prompt": r.capable_system_prompt,
        "efficient_system_prompt": r.efficient_system_prompt,
        "handoff_escalation_note": r.handoff_escalation_note,
        "handoff_deescalation_note": r.handoff_deescalation_note,
        "handoff_only_on_wrong_signal_escalation": r.handoff_only_on_wrong_signal_escalation,
        "stage_classifier_enabled": r.stage_classifier_enabled,
        "stage_classifier_target": r.stage_classifier_target,
        "stage_classifier_base_threshold": r.stage_classifier_base_threshold,
        "stage_classifier_threshold_step": r.stage_classifier_threshold_step,
        "stage_classifier_recent_turn_window": r.stage_classifier_recent_turn_window,
        "stage_classifier_prompt": r.stage_classifier_prompt,
        "stage_classifier_response_format_type": r.stage_classifier_response_format_type,
        "executor_target": r.executor_target,
        "advisor_target": r.advisor_target,
        "max_reviews": r.max_reviews,
        "gate_stall_turns": r.gate_stall_turns,
        "gate_trigger": r.gate_trigger,
        "gate_trigger_pattern": r.gate_trigger_pattern,
        "gate_min_tool_results": r.gate_min_tool_results,
        "advisor_max_tokens": r.advisor_max_tokens,
        "advisor_temperature": r.advisor_temperature,
        "transcript_max_chars": r.transcript_max_chars,
        "fail_open": r.fail_open,
        "reviewer_system_prompt": r.reviewer_system_prompt,
        "redo_feedback_prefix": r.redo_feedback_prefix,
        "type_label": route_type_label(r.type),
        "targets_summary": route_targets_summary(r),
        "is_passthrough": r.type == "passthrough",
    }


def _str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def provider_from_dict(d: dict) -> Provider:
    return Provider(
        name=_str(d.get("name")),
        display_name=_str(d.get("display_name")),
        endpoint=_str(d.get("endpoint")),
        api_key_env=_str(d.get("api_key_env")),
        api_key=_str(d.get("api_key")),
        max_retries=_int(d.get("max_retries"), 2),
        available_models=_str_list(d.get("available_models")),
        selected_models=_str_list(d.get("selected_models")),
    )


def route_from_dict(d: dict) -> Route:
    rtype = d.get("type") or "passthrough"
    if rtype not in ROUTE_TYPE_KEYS:
        rtype = "passthrough"
    return Route(
        name=_str(d.get("name")),
        id=_str(d.get("id")),
        type=rtype,
        target=_str(d.get("target")),
        targets=_str_list(d.get("targets")),
        weights=_str(d.get("weights")),
        seed=_str(d.get("seed"), "42"),
        classifier_target=_str(d.get("classifier_target")),
        weak_target=_str(d.get("weak_target")),
        strong_target=_str(d.get("strong_target")),
        base_threshold=_str(d.get("base_threshold"), "0.5"),
        threshold_step=_str(d.get("threshold_step"), "0.0"),
        classify_trigger=_str(d.get("classify_trigger"), "every_request"),
        message_hash_fallback=_str(d.get("message_hash_fallback"), "false"),
        prompt=_str(d.get("prompt")),
        response_format_type=_str(d.get("response_format_type"), "json_schema"),
        max_output_tokens=_str(d.get("max_output_tokens"), "4096"),
        recent_turn_window=_str(d.get("recent_turn_window")),
        confirmations=_str(d.get("confirmations"), "2"),
        window_message_chars=_str(d.get("window_message_chars"), "500"),
        default_target=_str(d.get("default_target")),
        response_schema=_str(d.get("response_schema")),
        policy_type=_str(d.get("policy_type"), "target_selector"),
        policy_selector=_str(d.get("policy_selector")),
        capable_target=_str(d.get("capable_target")),
        efficient_target=_str(d.get("efficient_target")),
        picker=_str(d.get("picker"), "efficient_first"),
        confidence_threshold=_str(d.get("confidence_threshold"), "0.5"),
        capable_system_prompt=_str(d.get("capable_system_prompt")),
        efficient_system_prompt=_str(d.get("efficient_system_prompt")),
        handoff_escalation_note=_str(d.get("handoff_escalation_note")),
        handoff_deescalation_note=_str(d.get("handoff_deescalation_note")),
        handoff_only_on_wrong_signal_escalation=_str(
            d.get("handoff_only_on_wrong_signal_escalation"), "true"
        ),
        stage_classifier_enabled=_str(d.get("stage_classifier_enabled"), "false"),
        stage_classifier_target=_str(d.get("stage_classifier_target")),
        stage_classifier_base_threshold=_str(
            d.get("stage_classifier_base_threshold"), "0.5"
        ),
        stage_classifier_threshold_step=_str(
            d.get("stage_classifier_threshold_step"), "0.1"
        ),
        stage_classifier_recent_turn_window=_str(
            d.get("stage_classifier_recent_turn_window"), "3"
        ),
        stage_classifier_prompt=_str(d.get("stage_classifier_prompt")),
        stage_classifier_response_format_type=_str(
            d.get("stage_classifier_response_format_type"), "json_schema"
        ),
        executor_target=_str(d.get("executor_target")),
        advisor_target=_str(d.get("advisor_target")),
        max_reviews=_str(d.get("max_reviews"), "3"),
        gate_stall_turns=_str(d.get("gate_stall_turns"), "30"),
        gate_trigger=_str(d.get("gate_trigger"), "no_tool_call"),
        gate_trigger_pattern=_str(d.get("gate_trigger_pattern")),
        gate_min_tool_results=_str(d.get("gate_min_tool_results"), "0"),
        advisor_max_tokens=_str(d.get("advisor_max_tokens"), "2048"),
        advisor_temperature=_str(d.get("advisor_temperature")),
        transcript_max_chars=_str(d.get("transcript_max_chars"), "200000"),
        fail_open=_str(d.get("fail_open"), "true"),
        reviewer_system_prompt=_str(d.get("reviewer_system_prompt")),
        redo_feedback_prefix=_str(d.get("redo_feedback_prefix")),
    )


# ---------------------------------------------------------------------------
# Validation (mirrors the TUI's _validate_provider / _validate_route)
# ---------------------------------------------------------------------------

def validate_provider(p: Provider, state: ConfigState, editing_idx: int = -1) -> str | None:
    if not p.name:
        return "Provider local name is required"
    if err := _run_validator(validate_toml_identifier, p.name, "Provider local name"):
        return err
    if not p.endpoint:
        return "Provider endpoint is required"
    if err := _run_validator(validate_toml_string_value, p.endpoint, "Provider endpoint"):
        return err
    if err := _run_validator(validate_env_var_name, p.api_key_env, "Provider env var"):
        return err
    if p.max_retries < 0:
        return f"Max retries must be >= 0 (got {p.max_retries})"
    for m in p.selected_models:
        if err := _run_validator(validate_toml_string_value, m, f"Model '{m}'"):
            return err
    for i, other in enumerate(state.providers):
        if i == editing_idx:
            continue
        if other.name == p.name:
            return f"Provider name '{p.name}' is already in use"
    return None


def _truthy(value: str) -> bool:
    """Read a form-string bool; empty/false/0 -> False."""
    if not value:
        return False
    return value.strip().lower() in ("true", "1", "yes", "on")


def _validate_enum(value: str, label: str, allowed: tuple[str, ...]) -> str | None:
    """Validate *value* is one of *allowed* (empty is OK = use default)."""
    v = value.strip()
    if not v:
        return None
    if v not in allowed:
        return f"{label} must be one of {', '.join(allowed)} (got '{v}')"
    return None


def _validate_stage_classifier_block(r: Route) -> str | None:
    """Validate the optional [routes.X.classifier] fallback block."""
    if not _truthy(r.stage_classifier_enabled):
        return None
    if not r.stage_classifier_target:
        return "Stage router classifier block requires a target (judge model)"
    if err := _run_validator(validate_float_field, 
        r.stage_classifier_base_threshold, "Classifier base threshold",
        minimum=0.0, maximum=1.0,
    ):
        return err
    if err := _run_validator(validate_float_field, 
        r.stage_classifier_threshold_step, "Classifier threshold step",
        minimum=0.0,
    ):
        return err
    if err := _run_validator(validate_int_field, 
        r.stage_classifier_recent_turn_window, "Classifier recent turn window",
        minimum=1,
    ):
        return err
    if err := _validate_enum(
        r.stage_classifier_response_format_type, "Classifier response format type",
        ("json_schema", "json_object"),
    ):
        return err
    return None


def validate_route(r: Route, state: ConfigState, editing_idx: int = -1) -> str | None:
    if not r.name:
        return "Route local name is required"
    if err := _run_validator(validate_toml_identifier, r.name, "Route local name"):
        return err
    if not r.id:
        return "Route ID is required"
    if err := _run_validator(validate_toml_string_value, r.id, "Route ID"):
        return err
    if not state.selected_models:
        return "No models available - configure providers and select models first"
    if r.id and r.id in r.model_refs():
        return f"Route {r.id} cannot reference itself as a target"
    for m in r.model_refs():
        if err := _run_validator(validate_toml_string_value, m, f"Route target '{m}'"):
            return err
    if r.type == "passthrough" and not r.target:
        return "Passthrough route requires a target"
    if r.type == "random" and len(r.targets) < 2:
        return "Random route requires at least 2 targets"
    if r.type == "random":
        if err := _run_validator(validate_weights, r.weights, "Weights", len(r.targets)):
            return err

    # ---- llm_classifier (capability, escalation, custom share validators) ----
    if r.type in (
        "llm_classifier_capability",
        "llm_classifier_escalation",
        "llm_classifier_custom",
    ):
        if not r.classifier_target:
            return "Classifier route requires a classifier/judge target"
        if err := _validate_enum(
            r.classify_trigger, "Classify trigger",
            ("every_request", "user_turn", "new_session"),
        ):
            return err
        if err := _validate_enum(
            r.response_format_type, "Response format type",
            ("json_schema", "json_object"),
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.max_output_tokens, "Max output tokens", minimum=1
        ):
            return err
        if _truthy(r.message_hash_fallback) and r.classify_trigger != "new_session":
            return "message_hash_fallback requires classify_trigger = new_session"

    if r.type == "llm_classifier_capability" and (
        not r.weak_target or not r.strong_target
    ):
        return "Capability classifier requires weak and strong targets"
    if r.type == "llm_classifier_capability":
        if err := _run_validator(validate_float_field, 
            r.base_threshold, "Threshold", minimum=0.0, maximum=1.0
        ):
            return err
        if err := _run_validator(validate_float_field, 
            r.threshold_step, "Threshold step", minimum=0.0
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.recent_turn_window, "Recent turn window", minimum=0
        ):
            return err

    if r.type == "llm_classifier_escalation" and (
        not r.weak_target or not r.strong_target
    ):
        return "Escalation classifier requires weak and strong targets"
    if r.type == "llm_classifier_escalation":
        if err := _run_validator(validate_int_field, r.confirmations, "Confirmations", minimum=1):
            return err
        if err := _run_validator(validate_int_field, 
            r.recent_turn_window, "Turn window", minimum=1
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.window_message_chars, "Window message chars", minimum=50
        ):
            return err

    if r.type == "llm_classifier_custom":
        if len(r.targets) < 2:
            return "Custom classifier requires at least 2 routing targets"
        if not r.response_schema.strip():
            return "Custom classifier requires a response_schema (JSON Schema)"
        if not r.policy_selector.strip():
            return "Custom classifier requires a policy selector (e.g. /decision/target)"
        if not r.default_target:
            return "Custom classifier requires a default_target"

    # ---- stage_router ----
    if r.type == "stage_router" and (
        not r.capable_target or not r.efficient_target
    ):
        return "Stage router requires capable and efficient targets"
    if r.type == "stage_router":
        if err := _validate_enum(
            r.picker, "Picker", ("efficient_first", "capable_first")
        ):
            return err
        if err := _run_validator(validate_float_field, 
            r.confidence_threshold, "Confidence", minimum=0.0, maximum=1.0
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.recent_turn_window, "Recent turn window", minimum=1
        ):
            return err
        if err := _validate_stage_classifier_block(r):
            return err

    # ---- advisor ----
    if r.type == "advisor" and (
        not r.executor_target or not r.advisor_target
    ):
        return "Advisor gate requires executor and advisor targets"
    if r.type == "advisor":
        if err := _run_validator(validate_int_field, r.max_reviews, "Max reviews", minimum=1):
            return err
        if err := _run_validator(validate_int_field, 
            r.gate_stall_turns, "Stall turns", minimum=0
        ):
            return err
        if err := _validate_enum(
            r.gate_trigger, "Gate trigger", ("no_tool_call", "pattern")
        ):
            return err
        if r.gate_trigger == "pattern" and not r.gate_trigger_pattern.strip():
            return "Gate trigger 'pattern' requires gate_trigger_pattern"
        if err := _run_validator(validate_int_field, 
            r.gate_min_tool_results, "Gate min tool results", minimum=0
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.advisor_max_tokens, "Advisor max tokens", minimum=1
        ):
            return err
        if err := _run_validator(validate_float_field, 
            r.advisor_temperature, "Advisor temperature", minimum=0.0
        ):
            return err
        if err := _run_validator(validate_int_field, 
            r.transcript_max_chars, "Transcript max chars", minimum=1
        ):
            return err

    # ---- composite ----
    if r.type == "composite" and (
        not r.capable_target or not r.efficient_target
    ):
        return "Composite route requires capable and efficient stage targets"
    if r.type == "composite":
        if not r.stage_classifier_target:
            return "Composite route requires a classifier judge target"
        if err := _run_validator(validate_float_field, 
            r.confidence_threshold, "Confidence", minimum=0.0, maximum=1.0
        ):
            return err
        if err := _validate_enum(
            r.classify_trigger, "Classify trigger",
            ("every_request", "user_turn", "new_session"),
        ):
            return err
        if err := _run_validator(validate_float_field, 
            r.stage_classifier_base_threshold, "Classifier base threshold",
            minimum=0.0, maximum=1.0,
        ):
            return err

    for i, other in enumerate(state.routes):
        if i == editing_idx:
            continue
        if other.name == r.name:
            return f"Route name '{r.name}' is already in use"
    return None


def validate_for_save(state: ConfigState) -> str | None:
    """Shared pre-save checks: providers, models, routes, broken refs, cycles."""
    s = state
    if not s.providers:
        return "No providers configured"
    for p in s.providers:
        if not p.endpoint:
            return f"Provider '{p.name}' missing endpoint"
    if not s.selected_models:
        return "No models selected"
    if not s.routes:
        return "No routes configured"
    broken = broken_route_indices(s)
    if broken:
        names = [s.routes[i].name for i in broken]
        return (
            f"{len(broken)} route(s) reference missing models: "
            f"{', '.join(names)}. Edit them to pick available models."
        )
    cycles = find_route_cycles(s)
    if cycles:
        cycle_desc = "; ".join(" -> ".join(c) for c in cycles)
        return f"route cycle detected: {cycle_desc}"
    return None


def broken_route_indices(state: ConfigState) -> list[int]:
    """Indices of non-passthrough routes referencing a missing model."""
    available = set(state.selected_models)
    return [
        i for i, r in enumerate(state.routes)
        if r.type != "passthrough" and any(m not in available for m in r.model_refs())
    ]


# ---------------------------------------------------------------------------
# Config manager
# ---------------------------------------------------------------------------

class ConfigManager:
    """Holds the in-memory config state and all mutation operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = ConfigState()
        self.load_errors: list[str] = []
        self.draft_recovered = False
        self._load_initial()

    # --- Initial load (mirrors SwitchyardConfigApp.__init__) ---

    def _load_initial(self) -> None:
        routes_err: str | None = None
        meta: dict = {}
        model_extras: dict[str, dict] = {}

        if kube.in_cluster():
            # Kubernetes: the ConfigMap is the source of truth. First apply
            # the code-maintained seed ConfigMap if it changed (see kube.py).
            try:
                note = kube.sync_seed()
                if note:
                    print(f"[seed-sync] {note}")
            except RuntimeError as e:
                self.load_errors.append(str(e))
            data = kube.load_config_data()
            existing_routes, providers, model_extras, routes_err = parse_routes_text(
                data.get("routes.toml", ""), source="routes.toml"
            )
            try:
                parsed_meta = json.loads(data.get("provider_meta.json") or "{}")
                meta = parsed_meta if isinstance(parsed_meta, dict) else {}
            except json.JSONDecodeError:
                meta = {}
        else:
            existing_routes, providers, model_extras, routes_err = (
                load_existing_routes(C.ROUTES_TOML)
            )

        if existing_routes:
            self.state.routes = existing_routes
        self.state.providers = providers

        draft_err: str | None = None
        if C.ROUTES_DRAFT.exists():
            draft_routes, draft_providers, draft_extras, draft_err = (
                load_existing_routes(C.ROUTES_DRAFT)
            )
            if draft_routes or draft_providers:
                self.state.routes = draft_routes
                self.state.providers = draft_providers
                model_extras = draft_extras
                self.draft_recovered = True

        self.state.model_extras = model_extras

        self.load_errors = list(
            dict.fromkeys(msg for msg in (routes_err, draft_err) if msg)
        )

        # Apply provider display names from the sidecar metadata. Draft
        # metadata wins when a draft was recovered; otherwise the live
        # source (ConfigMap in Kubernetes, sidecar file locally).
        if C.PROVIDER_META_DRAFT.exists():
            apply_provider_meta(self.state.providers, C.PROVIDER_META_DRAFT)
        elif kube.in_cluster():
            apply_provider_meta_dict(self.state.providers, meta)
        elif C.PROVIDER_META_FILE.exists():
            apply_provider_meta(self.state.providers, C.PROVIDER_META_FILE)

        # Fill API keys: draft .env, then the shell env, then .env.
        # In Kubernetes the shell env holds the tokens injected via
        # secretKeyRef from the providers' Secrets.
        for p in self.state.providers:
            if not p.api_key and p.api_key_env:
                p.api_key = read_env_var_from(C.ENV_DRAFT, p.api_key_env)
                if not p.api_key:
                    p.api_key = os.environ.get(p.api_key_env, "")
                if not p.api_key:
                    p.api_key = read_env_var_from(C.ENV_FILE, p.api_key_env)

        # No default provider is added automatically. Users must add providers via the UI or by supplying them in values.yaml.

        self._sync()

    # --- Passthrough / self-provider sync ---

    def _sync(self) -> None:
        selected = set(self.state.selected_models)
        self.state.routes = [
            r for r in self.state.routes
            if not (r.type == "passthrough" and r.target and r.target not in selected)
        ]
        self.state.routes.extend(
            ensure_passthrough_routes(self.state.routes, self.state.selected_models)
        )
        sync_self_provider(self.state)

    # --- Snapshot / preview ---

    def snapshot(self) -> dict:
        s = self.state
        custom_route_indices = [
            i for i, r in enumerate(s.routes) if r.type != "passthrough"
        ]
        passthrough_count = len(s.routes) - len(custom_route_indices)
        return {
            "providers": [provider_to_dict(p) for p in s.providers],
            "routes": [route_to_dict(r) for r in s.routes],
            "custom_route_indices": custom_route_indices,
            "passthrough_count": passthrough_count,
            "selected_models": list(s.selected_models),
            "available_models": list(s.available_models),
            "models": self._models_summary(),
            "broken_route_indices": broken_route_indices(s),
            "cycles": find_route_cycles(s),
            "draft_recovered": self.draft_recovered,
            "load_errors": list(self.load_errors),
            "has_unsaved_changes": self.has_unsaved_changes(),
            "files": self._file_refs(),
        }

    def _models_summary(self) -> list[dict]:
        """Per-model rows for the Models tab: selected models of real
        (non-self) providers with their extra_body settings. The synthetic
        self provider (route chaining) is skipped — it has no upstream to
        tune. A model offered by several providers is listed once (first
        provider wins, matching target generation).

        Each row carries the matching hint (if any) from the definitions
        file. Matching happens here rather than in the browser because
        the patterns are Python-flavored regexes (e.g. ``(?i)`` inline
        flags) that JavaScript's RegExp rejects."""
        loaded = model_hints.load_model_hints()
        hint_list = loaded.get("hints", [])
        rows: list[dict] = []
        seen: set[str] = set()
        for p in self.state.providers:
            if p.name == C.SELF_PROVIDER_NAME:
                continue
            for m in p.selected_models:
                if m in seen:
                    continue
                seen.add(m)
                rows.append({
                    "model": m,
                    "provider": p.name,
                    "provider_label": provider_display_name(p),
                    "extra_body": dict(self.state.model_extras.get(m, {})),
                    "hint": model_hints.match_hint(m, hint_list),
                })
        return rows

    @staticmethod
    def _file_refs() -> dict[str, str]:
        """Where the config lives: ConfigMap in Kubernetes, files locally."""
        if kube.in_cluster():
            name = kube.configmap_name()
            return {
                "routes": f"configmap/{name}: routes.toml",
                "env": "Kubernetes Secrets (providers[].secret)",
                "meta": f"configmap/{name}: provider_meta.json",
            }
        return {
            "routes": display_path(str(C.ROUTES_TOML)),
            "env": display_path(str(C.ENV_FILE)),
            "meta": display_path(str(C.PROVIDER_META_FILE)),
        }

    def _state_with_passthrough(self) -> ConfigState:
        """Return a copy of the current state with auto‑generated passthrough routes.
        The web UI mirrors the original TUI wizard which added a passthrough route for
        every selected model that did not already have an explicit route. This helper
        creates those temporary routes so they appear in the preview and are saved
        when the user clicks *Save*.
        """
        # Work on a shallow copy – routes list will be extended, providers stay the same.
        from copy import deepcopy
        state_copy = deepcopy(self.state)
        # Generate missing passthrough routes based on currently selected models.
        new_routes = ensure_passthrough_routes(state_copy.routes, list(state_copy.selected_models))
        state_copy.routes.extend(new_routes)
        return state_copy

    def preview(self) -> dict:
        preview_state = self._state_with_passthrough()
        refs = self._file_refs()
        return {
            "ok": True,
            "toml": generate_toml(preview_state),
            "env": update_env_file(preview_state.providers),
            "routes_path": refs["routes"],
            "env_path": refs["env"],
            "meta_path": refs["meta"],
        }

    def has_unsaved_changes(self) -> bool:
        return (
            C.ROUTES_DRAFT.exists()
            or C.ENV_DRAFT.exists()
            or C.PROVIDER_META_DRAFT.exists()
        )

    # --- Provider operations ---

    def probe(self, endpoint: str, api_key: str) -> dict:
        """Probe a provider endpoint. Returns {ok, url?, models?, error?}."""
        if not endpoint.strip():
            return {"ok": False, "error": "Endpoint is required"}
        try:
            url, models = probe_endpoint(endpoint.strip(), api_key.strip())
            return {"ok": True, "url": url, "models": models}
        except Exception as e:  # probe_endpoint raises ConnectionError on failure
            return {"ok": False, "error": str(e)}

    def refresh_models(self) -> dict:
        """Fetch fresh model lists from all configured providers."""
        with self._lock:
            results: list[tuple[int, str, list[str] | None, str]] = []
            targets = [
                (i, p) for i, p in enumerate(self.state.providers)
                if p.name != C.SELF_PROVIDER_NAME
            ]
            with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
                futs = {
                    ex.submit(self._fetch_one, i, p): (i, p)
                    for i, p in targets
                }
                for fut in as_completed(futs):
                    results.append(fut.result())

            total = 0
            errors: list[str] = []
            successes: list[str] = []
            for idx, name, models, error in results:
                p = self.state.providers[idx]
                if models is not None:
                    p.available_models = models
                    p.selected_models = list(models)
                    total += len(models)
                    successes.append(f"{name}: {len(models)}")
                else:
                    errors.append(f"{name}: {error}")

            self._sync()
            write_draft(self.state)
            return {
                "ok": True,
                "total": total,
                "providers": successes,
                "errors": errors,
                "state": self.snapshot(),
            }

    @staticmethod
    def _fetch_one(i: int, p: Provider) -> tuple[int, str, list[str] | None, str]:
        if not p.endpoint:
            return i, p.name, None, "no endpoint configured"
        if p.api_key_env and not p.api_key:
            return i, p.name, None, f"{p.api_key_env} not set"
        try:
            models = fetch_models_from_endpoint(p.endpoint, p.api_key)
            return i, p.name, models, ""
        except Exception as e:
            return i, p.name, None, str(e)

    def upsert_provider(self, index: int, payload: dict) -> dict:
        """Add (index -1) or update (index >= 0) a provider."""
        with self._lock:
            p = provider_from_dict(payload)
            err = validate_provider(p, self.state, index)
            if err:
                return {"ok": False, "error": err}
            if index >= 0 and index < len(self.state.providers):
                self.state.providers[index] = p
            else:
                self.state.providers.append(p)
            self._sync()
            write_draft(self.state)
            return {"ok": True, "notification": "Provider updated" if index >= 0 else "Provider added",
                    "state": self.snapshot()}

    def delete_provider(self, index: int) -> dict:
        with self._lock:
            if index < 0 or index >= len(self.state.providers):
                return {"ok": False, "error": "Provider not found"}
            del self.state.providers[index]
            self._sync()
            write_draft(self.state)
            return {"ok": True, "notification": "Provider deleted", "state": self.snapshot()}

    # --- Route operations ---

    def upsert_route(self, index: int, payload: dict) -> dict:
        """Add (index -1) or update (index >= 0) a route."""
        with self._lock:
            r = route_from_dict(payload)
            err = validate_route(r, self.state, index)
            if err:
                return {"ok": False, "error": err}
            if index >= 0 and index < len(self.state.routes):
                self.state.routes[index] = r
            else:
                self.state.routes.append(r)
            write_draft(self.state)
            return {"ok": True, "notification": "Route updated" if index >= 0 else "Route added",
                    "state": self.snapshot()}

    def delete_route(self, index: int) -> dict:
        with self._lock:
            if index < 0 or index >= len(self.state.routes):
                return {"ok": False, "error": "Route not found"}
            del self.state.routes[index]
            write_draft(self.state)
            return {"ok": True, "notification": "Route deleted", "state": self.snapshot()}

    def resync(self) -> dict:
        with self._lock:
            self._sync()
            write_draft(self.state)
            return {"ok": True, "state": self.snapshot()}

    # --- Model extras (Models tab) ---

    # extra_body key: a TOML bare key segment (no dots/braces/quotes), so
    # the generated [targets.X.extra_body] table stays readable.
    _EXTRA_KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
    _MAX_EXTRA_KEYS = 24
    _MAX_EXTRA_JSON = 2048  # serialized size cap per model

    def set_model_extras(self, model: str, extra_body: object) -> dict:
        """Set (or clear, when empty) a model's extra_body.

        Values may be bool/int/float/str scalars or JSON-style nested
        structures (the free-form rows' "json" type) — everything the
        server's ``extra_body: BTreeMap<String, Value>`` accepts. Stored
        on the state and round-tripped through routes.toml on save.
        """
        with self._lock:
            model = (model or "").strip()
            if not model:
                return {"ok": False, "error": "Model is required"}
            if model not in self.state.selected_models:
                return {"ok": False, "error": f"Unknown or unselected model '{model}'"}
            if not isinstance(extra_body, dict):
                return {"ok": False, "error": "extra_body must be an object"}

            cleaned: dict = {}
            for key, value in extra_body.items():
                if not isinstance(key, str) or not self._EXTRA_KEY_RE.fullmatch(key):
                    return {"ok": False, "error":
                            f"Invalid extra key '{key}' - use letters, digits, '_' or '-'"}
                if len(key) > 64:
                    return {"ok": False, "error": f"Extra key '{key}' is longer than 64 chars"}
                if key in cleaned:
                    return {"ok": False, "error": f"Duplicate extra key '{key}'"}
                err = self._validate_extra_value(value, f"Value for '{key}'")
                if err:
                    return {"ok": False, "error": err}
                cleaned[key] = value
            if len(cleaned) > self._MAX_EXTRA_KEYS:
                return {"ok": False, "error":
                        f"At most {self._MAX_EXTRA_KEYS} extra keys per model"}
            try:
                if len(json.dumps(cleaned)) > self._MAX_EXTRA_JSON:
                    return {"ok": False, "error": "Extras are too large "
                            f"(>{self._MAX_EXTRA_JSON} bytes serialized)"}
            except (TypeError, ValueError):
                return {"ok": False, "error": "Extras are not valid JSON values"}

            if cleaned:
                self.state.model_extras[model] = cleaned
            else:
                self.state.model_extras.pop(model, None)
            write_draft(self.state)
            return {
                "ok": True,
                "notification": f"Model settings saved for {model}",
                "state": self.snapshot(),
            }

    @classmethod
    def _validate_extra_value(cls, value: object, label: str) -> str | None:
        """Recursively validate an extra_body value; return an error or None."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int) or isinstance(value, float):
            return None
        if isinstance(value, str):
            if _run_validator(validate_toml_string_value, value, label):
                return f"{label} may not contain double quotes, backslashes, or control characters"
            return None
        if isinstance(value, list):
            if len(value) > 32:
                return f"{label}: lists are limited to 32 items"
            for item in value:
                err = cls._validate_extra_value(item, label)
                if err:
                    return err
            return None
        if isinstance(value, dict):
            if len(value) > 32:
                return f"{label}: objects are limited to 32 keys"
            for k, v in value.items():
                if not isinstance(k, str) or not k:
                    return f"{label}: object keys must be non-empty strings"
                err = cls._validate_extra_value(v, f"{label}.{k}")
                if err:
                    return err
            return None
        return f"{label}: unsupported type (use true/false, number, string, or JSON)"

    # --- Save / restart ---

    def save(self) -> dict:
        """Validate and persist the configuration.

        In Kubernetes, ``routes.toml`` / ``provider_meta.json`` are PATCHed
        into the config ConfigMap; provider tokens entered in the UI are
        upserted into the UI-managed token Secret, which both deployments
        load via ``envFrom`` (locally the original ``.env`` file is kept).

        The TUI automatically added missing passthrough routes before saving; the
        web UI now mirrors that behaviour by generating them on‑the‑fly.
        """
        with self._lock:
            # Build a temporary state that includes autogenerated passthrough routes.
            temp_state = self._state_with_passthrough()
            err = validate_for_save(temp_state)
            if err:
                title = (
                    "Broken Routes" if "missing models" in err
                    else "Cycle Detected" if "cycle" in err
                    else "Validation"
                )
                return {"ok": False, "error": err, "title": title}
            try:
                if kube.in_cluster():
                    lines = self._save_to_configmap(temp_state)
                else:
                    lines = self._save_to_files(temp_state)
                clear_draft()
                self.draft_recovered = False
            except (OSError, RuntimeError) as e:
                return {"ok": False, "error": f"Save failed: {e}", "title": "Save Failed"}

            return {
                "ok": True,
                "message": "Configuration saved!",
                "files": lines,
                "restart_available": is_switchyard_running(),
                "state": self.snapshot(),
            }

    def _save_to_configmap(self, temp_state: ConfigState) -> list[str]:
        """Persist routes.toml + provider_meta.json into the config ConfigMap.

        Provider tokens entered in the UI go into the UI-managed token
        Secret (``kube.upsert_token_secret``), from where both deployments
        load them via ``envFrom`` — without this the values would only
        exist in memory and the ephemeral draft.
        """
        toml_text = generate_toml(temp_state)
        meta_text = provider_meta_json(temp_state.providers)
        data = {"routes.toml": toml_text}
        if meta_text:
            data["provider_meta.json"] = meta_text
        kube.patch_config_data(data)
        name = kube.configmap_name()
        lines = [f"configmap/{name}: routes.toml"]
        if meta_text:
            lines.append(f"configmap/{name}: provider_meta.json")
        tokens = {
            p.api_key_env: p.api_key
            for p in temp_state.providers
            if p.api_key_env and p.api_key
        }
        if tokens:
            kube.upsert_token_secret(tokens)
            lines.append(
                f"secret/{kube.token_secret_name()}: "
                + ", ".join(sorted(tokens))
            )
        return lines

    def _save_to_files(self, temp_state: ConfigState) -> list[str]:
        """Original local save: write files with timestamped backups."""
        routes_bak = rotate_backups(C.ROUTES_TOML)
        env_bak = rotate_backups(C.ENV_FILE)
        meta_bak = (
            rotate_backups(C.PROVIDER_META_FILE)
            if load_provider_meta(C.PROVIDER_META_FILE)
            else None
        )
        C.ROUTES_TOML.parent.mkdir(parents=True, exist_ok=True)
        C.ROUTES_TOML.write_text(generate_toml(temp_state))
        C.ENV_FILE.write_text(update_env_file(temp_state.providers))
        save_provider_meta(temp_state.providers, C.PROVIDER_META_FILE)
        lines = [
            display_path(str(C.ROUTES_TOML)),
            display_path(str(C.ENV_FILE)),
            display_path(str(C.PROVIDER_META_FILE)),
        ]
        for name, bak in (
            ("routes", routes_bak),
            ("env", env_bak),
            ("meta", meta_bak),
        ):
            if bak:
                lines.append(f"Backup {name}: {display_path(str(bak))}")
        return lines

    def restart(self) -> dict:
        ok, msg = restart_switchyard()
        return {"ok": ok, "message": msg}

    def status(self) -> dict:
        return {
            "switchyard_running": is_switchyard_running(),
            "has_unsaved_changes": self.has_unsaved_changes(),
            # "kubernetes" when deployed by this chart (it sets
            # DEPLOYMENT_PLATFORM); empty for local/compose runs.
            "platform": os.environ.get("DEPLOYMENT_PLATFORM", ""),
        }

    def cycles(self) -> list[list[str]]:
        """Return any route dependency cycles detected in the current state."""
        from .routes import find_route_cycles
        return find_route_cycles(self.state)

# Module-level singleton shared by all requests.
manager = ConfigManager()
