# Switchyard routing notes

Notes on which Switchyard route types latch (sticky for a session) vs.
re-decide per turn. Source: upstream
[switchyard-server README](https://github.com/NVIDIA-NeMo/Switchyard/blob/main/crates/switchyard-server/README.md)
and
[stage_router docs](https://github.com/NVIDIA-NeMo/Switchyard/blob/main/docs/routing_algorithms/stage_router_routing.md).

## Goal

Route some turns of a conversation to a weak/dumb model and others to a
strong/smart model **mid-conversation**, without locking the whole session to
one target.

## Route types at a glance

| Route type            | Latches? | Mid-conversation switching? | Extra LLM call/turn? |
|-----------------------|----------|------------------------------|----------------------|
| `passthrough`         | n/a      | No (single target)           | No                   |
| `random`              | No       | Yes (per-call weighted)      | No                   |
| `llm_classifier` (`capability`) | Depends on `classify_trigger` | Yes if `every_request` | Yes (judge call) |
| `llm_classifier` (`escalation`) | Yes; sticky once escalated | No | Yes (judge call) |
| `stage_router`        | No       | Yes (per-turn, signal-driven) | No                   |
| `advisor_gate`        | No       | Yes (executor + advisor review loop) | Yes (advisor call) |

## `llm_classifier`: `classify_trigger` modes

The same route type behaves very differently depending on `classify_trigger`:

| `classify_trigger` | Latches? | Scope |
|---|---|---|
| `every_request` | No | Judges every call independently, including tool continuations |
| `user_turn` | Per turn | Judges on each new user message; **holds that target across tool calls** in between |
| `new_session` | Yes | Judges once, **reuses that target for the session** |

- `mode = "capability"`: judge scores solve-probability; routes to `weak_target`
  or `strong_target` against `base_threshold`. Non-latching in `every_request`
  mode.
- `mode = "escalation"`: escalates to `strong_target` after N confirmations
  within a turn window. **Strongly latched**; the README says "Session affinity
  retains a decision for the process lifetime, including a `strong_target`
  fallback produced while the judge was unreachable."
- Optional `message_hash_fallback = true` (requires `new_session`) extends
  affinity to callers with no session header by keying on the first user
  message. Note: unrelated callers sending identical text share one assignment.

## `stage_router` (recommended for non-latched mid-conversation routing)

Per-turn routing based on **tool-result history** already in the conversation:
no extra classifier call per turn. Switches back and forth as the agent moves
between exploration and routine implementation.

### Signals (corroborative, `tanh`-squashed to `[0, 1]`)

- **WRONG → capable**: `severity` (windowed error severity), `spinning`
  (deep churn, no reads/writes), `exploring` (reading/planning without
  producing).
- **PROGRESS → efficient**: `recent_production_intensity` (writes/edits
  landing over the recent window).
- A critical-error severity is a **hard override** that escalates on its own.

### Decision cascade

1. `confidence >= confidence_threshold` → signals pick capable/efficient.
2. Else if classifier block configured → classifier picks.
3. Else → picker default tier.

### Pickers

- `efficient_first` (default, cost-first): efficient is the default; escalate to
  capable only on a confident capable signal.
- `capable_first` (experimental, quality-first): capable is the default; drop
  to efficient only on a confident efficient signal. **Not benchmarked**;
  server logs a startup warning.

### Tuning `confidence_threshold`

| Threshold | Classifier? | Typical use |
|---|---|---|
| `0.0` | No | Cost/latency-sensitive. Accept every signal verdict. Critical errors still escalate. |
| `0.5` | No | **Recommended starting point.** Corroborative: one full wrong signal scores ~0.46, just under 0.5, so escalation takes a strong signal + corroboration. From SWE-Bench Pro Python-75 calibration. |
| `0.7`–`0.9` | Yes | Sub-threshold turns go to the LLM classifier. |
| `1.0` | Required | Tool signals only apply hard overrides; all other turns reach the classifier. |

### Optional classifier fallback

Add `[routes.stage.classifier]` and raise `confidence_threshold` above `0.0`.
The classifier is consulted only for sub-threshold turns. Give the classifier
its own LLM client/quota bucket; sharing one with the efficient tier adds a
request per classified turn and can cause sustained 429s at scale.

### Optional extras

- `handoff_notes`: `escalation_note` / `deescalation_note` sent to the tier the
  router switches to. `only_on_wrong_signal_escalation = true` (default).
- Per-tier system prompts: `capable_system_prompt`, `efficient_system_prompt`.

### Decision sources (visible in `/v1/stats`)

| Source           | When |
|------------------|------|
| `override`       | Critical-error severity (or context-compaction marker) forced capable. |
| `tests_passed`   | Settled run: recent test pass + recent write + no windowed error → efficient. |
| `dimensions`     | Corroborative scorer crossed threshold; sign of score picked tier. |
| `llm-classifier` | Signals ambiguous; classifier returned a verdict. |
| `fall_open`      | Signals ambiguous and classifier failed/unconfigured; default tier used. |

### When NOT to use `stage_router`

- Single-model deployment → use `passthrough`.
- Probabilistic A/B splits → use `random`.
- No tool-result history (pure chat) → every ambiguous turn lands on the
  picker's default tier; use `llm_classifier` with `every_request` instead.

## Summary

- Want non-latched, signal-driven, no extra LLM call/turn → **`stage_router`**
  with `efficient_first` (or `capable_first` if you accept the experimental
  caveat).
- Want non-latched but willing to pay a judge call per turn →
  **`llm_classifier`** with `mode = "capability"`,
  `classify_trigger = "every_request"`.
- Want sticky-per-session (smart-first or dumb-first, locked in) →
  `llm_classifier` with `classify_trigger = "new_session"`.
- Want one-way escalation (dumb → smart, never back) → `mode = "escalation"`,
  but understand it latches for the process lifetime.