# Task 18 — Granite explanation and anomaly-intent adapter

## Objective

Add a bounded IBM Granite adapter for explanations and anomaly intent parsing. The application must remain fully functional with the adapter unconfigured.

## Configuration placeholders

Read only:

```text
AGCC_GRANITE_BASE_URL
AGCC_GRANITE_API_KEY
AGCC_GRANITE_MODEL_ID
AGCC_GRANITE_PROJECT_ID
```

Do not supply defaults, URLs, model IDs, or secrets. Missing configuration activates `NotConfiguredGraniteClient`.

## Explanation requests

Supported tasks:

- Explain initial contact selection.
- Explain predicted shortfall.
- Explain replan proposal.
- Explain approved plan delta.

Input contains only deterministic facts: entities, volumes, times, costs, added/removed contacts, rejection codes, and algorithm-produced reasons.

Output schema:

```text
summary
impact
action
tradeoff
fact_references
```

Maximum 140 words. No factual assertion may omit a fact reference. If schema validation or fact-reference validation fails, discard the response and use deterministic template prose.

## Anomaly parsing

The model extracts only the fields allowed by Task 13. Explicit numerical degradation may be copied from user text. Qualitative severity is mapped by deterministic policy after parsing. Missing fields produce follow-up questions.

## Prohibited behavior

Granite may not:

- Select a station.
- Modify a plan.
- Choose a multiplier.
- Approve a proposal.
- Invent cost, weather, provider, or hardware facts.

## Acceptance

Test unconfigured fallback, valid schema, unsupported claims, missing fact references, prompt-injection-like anomaly text, explicit percentage extraction, and deterministic template fallback. Return the completion report and stop.

