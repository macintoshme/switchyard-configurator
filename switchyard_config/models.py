"""Dataclasses and route type tables for the switchyard configurator."""

from __future__ import annotations

from dataclasses import dataclass, field


# Route type -> (label, toml type, optional toml mode)
ROUTE_TYPES: list[tuple[str, str, str]] = [
    ("Passthrough", "passthrough", ""),
    ("Random (A/B Split)", "random", ""),
    ("LLM Classifier - Capability", "llm_classifier", "capability"),
    ("LLM Classifier - Escalation", "llm_classifier", "escalation"),
    ("LLM Classifier - Custom", "llm_classifier", "custom"),
    ("Stage Router", "stage_router", ""),
    ("Advisor Gate", "advisor", ""),
    ("Composite (Classifier -> Stage)", "composite", ""),
]

# Internal type keys used in Route.type (one per ROUTE_TYPES entry)
ROUTE_TYPE_KEYS: list[str] = [
    "passthrough",
    "random",
    "llm_classifier_capability",
    "llm_classifier_escalation",
    "llm_classifier_custom",
    "stage_router",
    "advisor",
    "composite",
]

# One-paragraph summary of each route type, shown in the form when the user
# picks a type. Summarized from the upstream Switchyard documentation at
# https://github.com/NVIDIA-NeMo/Switchyard/blob/main/crates/switchyard-server/README.md
ROUTE_TYPE_DESCRIPTIONS: dict[str, str] = {
    "passthrough": (
        "Direct to a single target. The route id is the upstream model name "
        "clients send (e.g. GLM-5.2). No routing logic — just forwards the "
        "request. Useful for exposing a model through switchyard's metrics "
        "and translation layer without any selection logic."
    ),
    "random": (
        "Weighted random selection across multiple targets. Weights are "
        "relative and do not need to sum to one. Useful for A/B testing, "
        "load balancing, or canary deployments. Omit weights for equal "
        "weighting; set a seed to reproduce a selection sequence."
    ),
    "llm_classifier_capability": (
        "A judge model scores each task's difficulty and routes to a weak "
        "or strong target based on a threshold. Anything the judge cannot "
        "decide routes to the strong target. Good for cost optimization "
        "when simple tasks don't need the strong model. The judge runs "
        "every request, each user turn, or once per session (configurable)."
    ),
    "llm_classifier_escalation": (
        "Like capability, but escalates to the strong target after N judge "
        "confirmations within a turn window. Keeps sessions on the weak "
        "model until it's clearly struggling, then upgrades for the rest "
        "of the session. Good for agent workflows where difficulty only "
        "becomes apparent mid-conversation."
    ),
    "llm_classifier_custom": (
        "A custom-mode classifier: the judge returns a JSON object matching "
        "a schema you supply, and a policy reads a selector from that "
        "verdict to pick one of several named targets. Good for routing "
        "across more than two tiers or for domain-specific selection rules."
    ),
    "stage_router": (
        "Scores tool-result and agent-progress signals from recent turns "
        "to pick a capable or efficient tier per turn, without an extra "
        "classifier call on every turn. Good for agentic workloads where "
        "the tool-call pattern indicates difficulty. An optional "
        "capability judge can be consulted when signals are undecided."
    ),
    "advisor": (
        "An executor target runs, then an advisor reviews its output; "
        "gates further execution after max reviews or stall turns. The "
        "advisor is judge-only and never a routing destination. Good for "
        "quality control on agent outputs where a second opinion should "
        "catch mistakes before the user sees them."
    ),
    "composite": (
        "Composes an LLM classifier with a stage router: the classifier "
        "picks the tier per user turn (or once per session), and the stage "
        "router runs the tool-execution loop on its own signals until the "
        "next user turn. Good for interactive coding agents in auto-mode "
        "where the opening prompt and the tool loop each warrant their own "
        "routing decision."
    ),
}


@dataclass
class Route:
    """A single configured route. Fields are strings for form compatibility."""

    name: str = ""  # local TOML table name (e.g., "smart", "fast")
    id: str = ""  # model ID clients send (e.g., "switchyard/smart")
    type: str = "passthrough"  # one of ROUTE_TYPE_KEYS

    # passthrough
    target: str = ""

    # random
    targets: list[str] = field(default_factory=list)
    weights: str = ""
    seed: str = "42"  # reproducible selection; override via TOML edit

    # llm_classifier (shared by capability, escalation, and custom modes)
    classifier_target: str = ""
    weak_target: str = ""
    strong_target: str = ""
    base_threshold: str = "0.5"
    threshold_step: str = "0.0"  # capability: added per boundary step
    classify_trigger: str = "every_request"  # every_request|user_turn|new_session
    message_hash_fallback: str = "false"  # bool; requires new_session
    prompt: str = ""  # route-level override of the packaged judge prompt
    response_format_type: str = "json_schema"  # json_schema|json_object
    max_output_tokens: str = "4096"  # judge reply budget
    recent_turn_window: str = ""  # capability: trailing msgs judge sees

    # escalation-specific (emitted under [routes.X.escalation])
    confirmations: str = "2"
    # recent_turn_window is shared; escalation default applied at emit time
    window_message_chars: str = "500"

    # llm_classifier custom mode
    default_target: str = ""  # fallback when verdict misses/unknown
    response_schema: str = ""  # inner JSON Schema (multiline string)
    policy_type: str = "target_selector"  # only supported policy today
    policy_selector: str = ""  # jsonptr into the verdict, e.g. /decision/target

    # stage_router
    capable_target: str = ""
    efficient_target: str = ""
    picker: str = "efficient_first"
    confidence_threshold: str = "0.5"
    # recent_turn_window is shared; stage default (3) applied at emit time
    capable_system_prompt: str = ""
    efficient_system_prompt: str = ""

    # stage_router [routes.X.handoff_notes] block
    handoff_escalation_note: str = ""
    handoff_deescalation_note: str = ""
    handoff_only_on_wrong_signal_escalation: str = "true"  # bool

    # stage_router [routes.X.classifier] fallback block (also used by composite)
    stage_classifier_enabled: str = "false"  # bool; emit the block when true
    stage_classifier_target: str = ""  # judge model (not a routing destination)
    stage_classifier_base_threshold: str = "0.5"
    stage_classifier_threshold_step: str = "0.1"
    stage_classifier_recent_turn_window: str = "3"
    stage_classifier_prompt: str = ""
    stage_classifier_response_format_type: str = "json_schema"

    # advisor
    executor_target: str = ""
    advisor_target: str = ""
    max_reviews: str = "3"
    gate_stall_turns: str = "30"
    gate_trigger: str = "no_tool_call"  # no_tool_call|pattern
    gate_trigger_pattern: str = ""  # required when gate_trigger = "pattern"
    gate_min_tool_results: str = "0"  # min tool results before a no_tool_call review
    advisor_max_tokens: str = "2048"
    advisor_temperature: str = ""  # unset when empty
    transcript_max_chars: str = "200000"
    fail_open: str = "true"  # bool; advisor failure passes the turn through
    reviewer_system_prompt: str = ""  # overrides APPROVE/REDO contract
    redo_feedback_prefix: str = ""  # overrides prefix before a REDO plan

    def toml_type(self) -> str:
        """Return the TOML route type for this route."""
        for key, (_, toml_type, _) in zip(ROUTE_TYPE_KEYS, ROUTE_TYPES):
            if key == self.type:
                return toml_type
        return "passthrough"

    def toml_mode(self) -> str:
        """Return the TOML mode for this route, or empty string if none."""
        for key, (_, _, mode) in zip(ROUTE_TYPE_KEYS, ROUTE_TYPES):
            if key == self.type:
                return mode
        return ""

    def model_refs(self) -> list[str]:
        """Return all model IDs referenced by this route (in order, deduped)."""
        if self.type == "passthrough":
            return [self.target] if self.target else []
        if self.type == "random":
            return list(self.targets)
        if self.type in ("llm_classifier_capability", "llm_classifier_escalation"):
            return [
                m for m in (
                    self.classifier_target,
                    self.weak_target,
                    self.strong_target,
                ) if m
            ]
        if self.type == "llm_classifier_custom":
            refs = [self.classifier_target]
            refs.extend(self.targets)
            if self.default_target:
                refs.append(self.default_target)
            return list(dict.fromkeys(r for r in refs if r))
        if self.type == "stage_router":
            return [
                m for m in (self.capable_target, self.efficient_target) if m
            ]
        if self.type == "advisor":
            return [
                m for m in (self.executor_target, self.advisor_target) if m
            ]
        if self.type == "composite":
            # classifier judge + stage capable/efficient tiers
            refs = [self.stage_classifier_target,
                    self.capable_target, self.efficient_target]
            return list(dict.fromkeys(r for r in refs if r))
        return []


@dataclass
class Provider:
    """An LLM provider endpoint (e.g., OpenAI, Ollama, LM Studio, vLLM)."""

    name: str = ""  # TOML table key, e.g., "openai", "ollama"
    display_name: str = ""  # human-readable label, e.g., "OpenAI", "vLLM"
    endpoint: str = ""  # full URL, e.g., "https://api.openai.com/v1"
    api_key_env: str = ""  # env var name, e.g., "OPENAI_API_KEY"
    api_key: str = ""  # actual key value (stored in .env, not routes.toml)
    available_models: list[str] = field(default_factory=list)
    selected_models: list[str] = field(default_factory=list)
    max_retries: int = 2  # upstream transport retry count


@dataclass
class ConfigState:
    providers: list[Provider] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)

    @property
    def selected_models(self) -> list[str]:
        """All selected models across all providers (deduped, order-preserving)."""
        return list(dict.fromkeys(
            m for p in self.providers for m in p.selected_models
        ))

    @property
    def available_models(self) -> list[str]:
        """All available models across all providers (deduped)."""
        return list(dict.fromkeys(
            m for p in self.providers for m in p.available_models
        ))

    def model_to_provider(self) -> dict[str, str]:
        """Map each selected model ID to its provider name (first wins)."""
        mapping: dict[str, str] = {}
        for p in self.providers:
            for m in p.selected_models:
                if m not in mapping:
                    mapping[m] = p.name
        return mapping