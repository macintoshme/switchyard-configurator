"""Route‑type registry.

Each supported route type registers a ``RouteHandler`` that knows how to
* validate a ``Route`` instance,
* serialise it to the TOML representation expected by Switchyard, and
* optionally expose a UI schema for the front‑end.

The registry makes it trivial to add new route types without touching the
core ``generate_toml`` implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from .models import Route, ConfigState
from .validation import ValidationError
from .routes import (
    validate_weights,
    validate_float_field,
    validate_int_field,
    validate_toml_string_value,
    _handoff_notes_block,
    _stage_classifier_block,
    _parse_bool,
)

# Type alias for the ``tn`` helper used in TOML generation (model_id -> target name)
TargetNameFn = Callable[[str], str]


class RouteHandler:
    """Base class for all route‑type handlers.

    Sub‑classes must define ``key`` (the internal ``Route.type`` value) and provide
    ``validate`` and ``to_toml`` implementations.
    """

    key: str
    label: str
    description: str

    def validate(self, route: Route, state: ConfigState) -> None:
        """Validate *route*; raise :class:`ValidationError` on failure."""
        raise NotImplementedError

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        """Return the TOML dict for *route*.

        ``tn`` converts a model identifier to the generated target name.
        """
        raise NotImplementedError

    def ui_schema(self) -> dict:
        """Optional JSON schema describing the fields for this route type.
        The web UI can fetch this to render dynamic forms.
        """
        return {}


# ---------------------------------------------------------------------------
# Built‑in handler: Passthrough
# ---------------------------------------------------------------------------

class PassthroughHandler(RouteHandler):
    key = "passthrough"
    label = "Passthrough"
    description = (
        "Direct to a single target. The route id is the upstream model name "
        "clients send (e.g. GLM‑5.2). No routing logic – just forwards the request."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        if not route.target:
            raise ValidationError(field="target", message="Passthrough route requires a target model ID")
        # No further validation needed – any string is accepted (other checks are done elsewhere).

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        if route.target:
            r["target"] = tn(route.target)
        return r

    def ui_schema(self) -> dict:
        return {
            "fields": [
                {"name": "target", "type": "string", "label": "Target model ID", "required": True},
            ]
        }


class RandomHandler(RouteHandler):
    key = "random"
    label = "Random (A/B Split)"
    description = (
        "Weighted random selection across multiple targets. "
        "Weights are relative and do not need to sum to one. Useful for A/B testing, "
        "load balancing, or canary deployments."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        # Must have at least two targets.
        if len(route.targets) < 2:
            raise ValidationError(field="targets", message="Random route requires at least two targets")
        # Validate weights if supplied.
        validate_weights(route.weights, "Weights", expected_count=len(route.targets))
        # Seed is optional – no validation needed (will default to 42).

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        if len(route.targets) >= 2:
            r["targets"] = [tn(m) for m in route.targets]
            if route.weights.strip():
                r["weights"] = [float(w.strip()) for w in route.weights.split(",") if w.strip()]
            seed_val = route.seed.strip() or "42"
            r["seed"] = int(seed_val)
        return r

    def ui_schema(self) -> dict:
        return {
            "fields": [
                {"name": "targets", "type": "list", "label": "Target model IDs", "required": True},
                {"name": "weights", "type": "string", "label": "Comma‑separated weights (optional)"},
                {"name": "seed", "type": "string", "label": "Seed (optional)"},
            ]
        }


class LlmClassifierCapabilityHandler(RouteHandler):
    key = "llm_classifier_capability"
    label = "LLM Classifier – Capability"
    description = (
        "Scores each request's difficulty and routes to a weak or strong target based on a threshold."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        # Required fields
        if not route.classifier_target:
            raise ValidationError(field="classifier_target", message="Classifier target is required")
        if not route.weak_target:
            raise ValidationError(field="weak_target", message="Weak target is required")
        if not route.strong_target:
            raise ValidationError(field="strong_target", message="Strong target is required")
        # Base threshold and optional step
        validate_float_field(route.base_threshold, "Base threshold", minimum=0.0, maximum=1.0)
        if route.threshold_step.strip():
            validate_float_field(route.threshold_step, "Threshold step", minimum=0.0)
        # Optional recent_turn_window
        if route.recent_turn_window.strip():
            validate_int_field(route.recent_turn_window, "Recent turn window", minimum=1)
        # Optional max_output_tokens
        if route.max_output_tokens.strip():
            validate_int_field(route.max_output_tokens, "Max output tokens", minimum=1)
        # Optional prompt and response format
        if route.prompt.strip():
            validate_toml_string_value(route.prompt, "Prompt")
        if route.response_format_type.strip() and route.response_format_type != "json_schema":
            raise ValidationError(field="response_format_type", message="Only 'json_schema' is supported for now")
        # Classify trigger and message hash fallback
        if route.classify_trigger.strip() and route.classify_trigger != "every_request":
            raise ValidationError(field="classify_trigger", message="Only 'every_request' is supported currently")
        if route.message_hash_fallback.strip():
            raise ValidationError(field="message_hash_fallback", message="Message hash fallback not supported")

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        r["classifier_target"] = tn(route.classifier_target)
        r["weak_target"] = tn(route.weak_target)
        r["strong_target"] = tn(route.strong_target)
        if route.base_threshold.strip():
            r["base_threshold"] = float(route.base_threshold)
        if route.threshold_step.strip():
            r["threshold_step"] = float(route.threshold_step)
        if route.classify_trigger.strip() and route.classify_trigger != "every_request":
            r["classify_trigger"] = route.classify_trigger
        if route.message_hash_fallback.strip():
            r["message_hash_fallback"] = True
        if route.recent_turn_window.strip():
            r["recent_turn_window"] = int(route.recent_turn_window)
        if route.prompt.strip():
            r["prompt"] = route.prompt
        if route.response_format_type.strip() and route.response_format_type != "json_schema":
            r["response_format_type"] = route.response_format_type
        if route.max_output_tokens.strip():
            r["max_output_tokens"] = int(route.max_output_tokens)
        return r

    def ui_schema(self) -> dict:
        return {"fields": [
            {"name": "classifier_target", "type": "string", "label": "Classifier target", "required": True},
            {"name": "weak_target", "type": "string", "label": "Weak target", "required": True},
            {"name": "strong_target", "type": "string", "label": "Strong target", "required": True},
            {"name": "base_threshold", "type": "string", "label": "Base threshold (0‑1)"},
            {"name": "threshold_step", "type": "string", "label": "Threshold step (optional)"},
            {"name": "classify_trigger", "type": "string", "label": "Classify trigger (optional)"},
            {"name": "message_hash_fallback", "type": "string", "label": "Message hash fallback (optional)"},
            {"name": "recent_turn_window", "type": "string", "label": "Recent turn window (optional)"},
            {"name": "prompt", "type": "string", "label": "Prompt (optional)"},
            {"name": "response_format_type", "type": "string", "label": "Response format type (optional)"},
            {"name": "max_output_tokens", "type": "string", "label": "Max output tokens (optional)"},
        ]}


class LlmClassifierEscalationHandler(RouteHandler):
    key = "llm_classifier_escalation"
    label = "LLM Classifier – Escalation"
    description = (
        "Same as capability classifier but includes escalation settings (confirmations, windows)."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        # Reuse capability validation for required fields.
        LlmClassifierCapabilityHandler().validate(route, state)
        # Escalation‑specific fields.
        if route.confirmations.strip():
            validate_int_field(route.confirmations, "Confirmations", minimum=1)
        if route.recent_turn_window.strip():
            validate_int_field(route.recent_turn_window, "Escalation recent turn window", minimum=1)
        if route.window_message_chars.strip():
            validate_int_field(route.window_message_chars, "Window message chars", minimum=1)

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r = LlmClassifierCapabilityHandler().to_toml(route, tn)
        r["escalation"] = {
            "confirmations": int(route.confirmations) if route.confirmations.strip() else 2,
            "recent_turn_window": int(route.recent_turn_window) if route.recent_turn_window.strip() else 28,
            "window_message_chars": int(route.window_message_chars) if route.window_message_chars.strip() else 500,
        }
        return r

    def ui_schema(self) -> dict:
        base = LlmClassifierCapabilityHandler().ui_schema()["fields"]
        base.extend([
            {"name": "confirmations", "type": "string", "label": "Confirmations (optional)"},
            {"name": "recent_turn_window", "type": "string", "label": "Escalation recent turn window (optional)"},
            {"name": "window_message_chars", "type": "string", "label": "Window message chars (optional)"},
        ])
        return {"fields": base}


class LlmClassifierCustomHandler(RouteHandler):
    key = "llm_classifier_custom"
    label = "LLM Classifier – Custom"
    description = (
        "Custom classifier with arbitrary targets, default target, and a policy selector."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        # Required classifier target
        if not route.classifier_target:
            raise ValidationError(field="classifier_target", message="Classifier target is required")
        # Targets list (optional, but if present must have ≥2)
        if route.targets and len(route.targets) < 2:
            raise ValidationError(field="targets", message="Custom route with targets must have at least two")
        # Validate optional fields similarly to capability handler
        if route.base_threshold.strip():
            validate_float_field(route.base_threshold, "Base threshold", minimum=0.0, maximum=1.0)
        if route.threshold_step.strip():
            validate_float_field(route.threshold_step, "Threshold step", minimum=0.0)
        if route.policy_selector.strip() and not route.policy_type:
            raise ValidationError(field="policy_selector", message="Policy selector requires a policy type")
        # Prompt, response schema, etc.
        if route.prompt.strip():
            validate_toml_string_value(route.prompt, "Prompt")
        if route.response_schema.strip():
            # No strict validation – stored as‑is.
            pass
        if route.max_output_tokens.strip():
            validate_int_field(route.max_output_tokens, "Max output tokens", minimum=1)

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        r["classifier_target"] = tn(route.classifier_target)
        if route.targets:
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
        return r

    def ui_schema(self) -> dict:
        return {"fields": [
            {"name": "classifier_target", "type": "string", "label": "Classifier target", "required": True},
            {"name": "targets", "type": "list", "label": "Targets (optional)"},
            {"name": "default_target", "type": "string", "label": "Default target (optional)"},
            {"name": "prompt", "type": "string", "label": "Prompt (optional)"},
            {"name": "response_schema", "type": "string", "label": "Response schema (optional)"},
            {"name": "response_format_type", "type": "string", "label": "Response format type (optional)"},
            {"name": "classify_trigger", "type": "string", "label": "Classify trigger (optional)"},
            {"name": "max_output_tokens", "type": "string", "label": "Max output tokens (optional)"},
            {"name": "policy_type", "type": "string", "label": "Policy type (optional)"},
            {"name": "policy_selector", "type": "string", "label": "Policy selector (optional)"},
        ]}


class StageRouterHandler(RouteHandler):
    key = "stage_router"
    label = "Stage Router"
    description = (
        "Selects capable or efficient target based on model‑level signals; can include a fallback classifier."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        if not route.capable_target:
            raise ValidationError(field="capable_target", message="Capable target is required")
        if not route.efficient_target:
            raise ValidationError(field="efficient_target", message="Efficient target is required")
        if route.confidence_threshold.strip():
            validate_float_field(route.confidence_threshold, "Confidence threshold", minimum=0.0, maximum=1.0)
        if route.recent_turn_window.strip():
            validate_int_field(route.recent_turn_window, "Recent turn window", minimum=1)
        # Hand‑off notes are optional strings – no validation needed.
        # Classifier fallback (optional) – reuse existing validator logic.
        if route.stage_classifier_enabled.strip() and route.stage_classifier_enabled.lower() == "true":
            # Validate any fields that are present.
            if route.stage_classifier_target:
                # No further validation – just ensure it's a string.
                pass
            if route.stage_classifier_base_threshold.strip():
                validate_float_field(route.stage_classifier_base_threshold, "Stage classifier base threshold", minimum=0.0)
            if route.stage_classifier_threshold_step.strip():
                validate_float_field(route.stage_classifier_threshold_step, "Stage classifier threshold step", minimum=0.0)
            if route.stage_classifier_recent_turn_window.strip():
                validate_int_field(route.stage_classifier_recent_turn_window, "Stage classifier recent turn window", minimum=1)
            # Prompt and response format type optional.
            if route.stage_classifier_response_format_type.strip() and route.stage_classifier_response_format_type != "json_schema":
                raise ValidationError(field="stage_classifier_response_format_type", message="Only 'json_schema' supported")

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        r["capable_target"] = tn(route.capable_target)
        r["efficient_target"] = tn(route.efficient_target)
        r["picker"] = route.picker
        if route.confidence_threshold.strip():
            r["confidence_threshold"] = float(route.confidence_threshold)
        if route.recent_turn_window.strip():
            r["recent_turn_window"] = int(route.recent_turn_window)
        if route.capable_system_prompt.strip():
            r["capable_system_prompt"] = route.capable_system_prompt
        if route.efficient_system_prompt.strip():
            r["efficient_system_prompt"] = route.efficient_system_prompt
        hb = _handoff_notes_block(route)
        if hb is not None:
            r["handoff_notes"] = hb
        if route.stage_classifier_enabled.strip() and route.stage_classifier_enabled.lower() == "true":
            cb = _stage_classifier_block(route)
            if cb is not None:
                r["classifier"] = cb
        return r

    def ui_schema(self) -> dict:
        return {"fields": [
            {"name": "capable_target", "type": "string", "label": "Capable target", "required": True},
            {"name": "efficient_target", "type": "string", "label": "Efficient target", "required": True},
            {"name": "picker", "type": "string", "label": "Picker", "default": "efficient_first"},
            {"name": "confidence_threshold", "type": "string", "label": "Confidence threshold (optional)"},
            {"name": "recent_turn_window", "type": "string", "label": "Recent turn window (optional)"},
            {"name": "capable_system_prompt", "type": "string", "label": "Capable system prompt (optional)"},
            {"name": "efficient_system_prompt", "type": "string", "label": "Efficient system prompt (optional)"},
            # Hand‑off notes and classifier fallback are advanced; omitted from basic UI.
        ]}


class AdvisorHandler(RouteHandler):
    key = "advisor"
    label = "Advisor Gate"
    description = (
        "Runs an executor model, then an advisor model reviews the output before further execution."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        if not route.executor_target:
            raise ValidationError(field="executor_target", message="Executor target is required")
        if not route.advisor_target:
            raise ValidationError(field="advisor_target", message="Advisor target is required")
        # Integer fields with defaults.
        if route.max_reviews.strip():
            validate_int_field(route.max_reviews, "Max reviews", minimum=1)
        if route.gate_stall_turns.strip():
            validate_int_field(route.gate_stall_turns, "Gate stall turns", minimum=0)
        if route.gate_min_tool_results.strip():
            validate_int_field(route.gate_min_tool_results, "Gate min tool results", minimum=0)
        if route.advisor_max_tokens.strip():
            validate_int_field(route.advisor_max_tokens, "Advisor max tokens", minimum=1)
        # Optional fields.
        if route.gate_trigger.strip() and route.gate_trigger != "no_tool_call":
            raise ValidationError(field="gate_trigger", message="Only 'no_tool_call' supported")
        if route.gate_trigger_pattern.strip():
            # No deep validation – treat as a raw regex/string.
            pass
        if route.advisor_temperature.strip():
            try:
                float(route.advisor_temperature)
            except ValueError:
                raise ValidationError(field="advisor_temperature", message="Must be a number")
        if route.transcript_max_chars.strip():
            validate_int_field(route.transcript_max_chars, "Transcript max chars", minimum=1)
        if not _parse_bool(route.fail_open, True):
            # It's okay – just ensure it's a boolean string.
            pass
        # Reviewer system prompt and redo feedback prefix are optional strings.
        # No further validation needed.

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        r["executor_target"] = tn(route.executor_target)
        r["advisor_target"] = tn(route.advisor_target)
        r["max_reviews"] = int(route.max_reviews) if route.max_reviews.strip() else 3
        r["gate_stall_turns"] = int(route.gate_stall_turns) if route.gate_stall_turns.strip() else 30
        if route.gate_trigger.strip() and route.gate_trigger != "no_tool_call":
            r["gate_trigger"] = route.gate_trigger
        if route.gate_trigger_pattern.strip():
            r["gate_trigger_pattern"] = route.gate_trigger_pattern
        if route.gate_min_tool_results.strip():
            r["gate_min_tool_results"] = int(route.gate_min_tool_results)
        if route.advisor_max_tokens.strip():
            r["advisor_max_tokens"] = int(route.advisor_max_tokens)
        if route.advisor_temperature.strip():
            r["advisor_temperature"] = float(route.advisor_temperature)
        if route.transcript_max_chars.strip():
            r["transcript_max_chars"] = int(route.transcript_max_chars)
        if not _parse_bool(route.fail_open, True):
            r["fail_open"] = False
        if route.reviewer_system_prompt.strip():
            r["reviewer_system_prompt"] = route.reviewer_system_prompt
        if route.redo_feedback_prefix.strip():
            r["redo_feedback_prefix"] = route.redo_feedback_prefix
        return r

    def ui_schema(self) -> dict:
        return {"fields": [
            {"name": "executor_target", "type": "string", "label": "Executor target", "required": True},
            {"name": "advisor_target", "type": "string", "label": "Advisor target", "required": True},
            {"name": "max_reviews", "type": "string", "label": "Max reviews (optional)"},
            {"name": "gate_stall_turns", "type": "string", "label": "Gate stall turns (optional)"},
            {"name": "gate_trigger", "type": "string", "label": "Gate trigger (optional)"},
            {"name": "gate_trigger_pattern", "type": "string", "label": "Gate trigger pattern (optional)"},
            {"name": "gate_min_tool_results", "type": "string", "label": "Gate min tool results (optional)"},
            {"name": "advisor_max_tokens", "type": "string", "label": "Advisor max tokens (optional)"},
            {"name": "advisor_temperature", "type": "string", "label": "Advisor temperature (optional)"},
            {"name": "transcript_max_chars", "type": "string", "label": "Transcript max chars (optional)"},
            {"name": "fail_open", "type": "string", "label": "Fail open (optional)"},
            {"name": "reviewer_system_prompt", "type": "string", "label": "Reviewer system prompt (optional)"},
            {"name": "redo_feedback_prefix", "type": "string", "label": "Redo feedback prefix (optional)"},
        ]}


class CompositeHandler(RouteHandler):
    key = "composite"
    label = "Composite (Classifier → Stage)"
    description = (
        "Combines a classifier with a stage router; the classifier determines the tier, then the stage router selects the concrete model."
    )

    def validate(self, route: Route, state: ConfigState) -> None:
        # Validate stage‑router part.
        StageRouterHandler().validate(route, state)
        # Validate classifier fallback (same fields as stage classifier).
        if route.stage_classifier_enabled.strip() and route.stage_classifier_enabled.lower() == "true":
            # Reuse stage‑router validation for classifier fields.
            StageRouterHandler().validate(route, state)
        # No additional fields beyond those already validated.

    def to_toml(self, route: Route, tn: TargetNameFn) -> dict:
        r: dict[str, object] = {"id": route.id, "type": route.toml_type()}
        # Stage sub‑table
        stage_block: dict[str, object] = {}
        if route.capable_target:
            stage_block["capable_target"] = tn(route.capable_target)
        if route.efficient_target:
            stage_block["efficient_target"] = tn(route.efficient_target)
        if route.confidence_threshold.strip():
            stage_block["confidence_threshold"] = float(route.confidence_threshold)
        if route.recent_turn_window.strip():
            stage_block["recent_turn_window"] = int(route.recent_turn_window)
        if route.capable_system_prompt.strip():
            stage_block["capable_system_prompt"] = route.capable_system_prompt
        if route.efficient_system_prompt.strip():
            stage_block["efficient_system_prompt"] = route.efficient_system_prompt
        hb = _handoff_notes_block(route)
        if hb is not None:
            stage_block["handoff_notes"] = hb
        if stage_block:
            r["stage"] = stage_block
        # Classifier block – force‑enable for composite.
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
        return r

    def ui_schema(self) -> dict:
        # Reuse stage router UI + classifier fields.
        base = StageRouterHandler().ui_schema()["fields"]
        base.append({"name": "stage_classifier_target", "type": "string", "label": "Stage classifier target (optional)"})
        base.append({"name": "stage_classifier_base_threshold", "type": "string", "label": "Stage classifier base threshold (optional)"})
        base.append({"name": "stage_classifier_threshold_step", "type": "string", "label": "Stage classifier threshold step (optional)"})
        base.append({"name": "stage_classifier_recent_turn_window", "type": "string", "label": "Stage classifier recent turn window (optional)"})
        base.append({"name": "stage_classifier_prompt", "type": "string", "label": "Stage classifier prompt (optional)"})
        base.append({"name": "stage_classifier_response_format_type", "type": "string", "label": "Stage classifier response format type (optional)"})
        base.append({"name": "classify_trigger", "type": "string", "label": "Classify trigger (optional)"})
        base.append({"name": "message_hash_fallback", "type": "string", "label": "Message hash fallback (optional)"})
        return {"fields": base}


# Register all handlers – keys are the internal ``Route.type`` strings.
ROUTE_REGISTRY: Dict[str, RouteHandler] = {
    PassthroughHandler.key: PassthroughHandler(),
    RandomHandler.key: RandomHandler(),
    LlmClassifierCapabilityHandler.key: LlmClassifierCapabilityHandler(),
    LlmClassifierEscalationHandler.key: LlmClassifierEscalationHandler(),
    LlmClassifierCustomHandler.key: LlmClassifierCustomHandler(),
    StageRouterHandler.key: StageRouterHandler(),
    AdvisorHandler.key: AdvisorHandler(),
    CompositeHandler.key: CompositeHandler(),
    # Future handlers can be added here.
}



# Registry of all handlers – keys are the internal ``Route.type`` strings.
ROUTE_REGISTRY: Dict[str, RouteHandler] = {
    PassthroughHandler.key: PassthroughHandler(),
    # Future handlers can be added here.
}
