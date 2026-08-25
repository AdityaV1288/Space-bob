# Task 13 — Structured and natural-language anomaly proposals

## Objective

Implement deterministic anomaly policies and a Granite parsing boundary. Granite may extract intent but cannot choose numerical effects.

## Supported anomaly types

- `STATION_OUTAGE`: multiplier 0 for named station and interval.
- `RATE_DEGRADATION`: user supplies explicit percentage or chooses a configured severity.
- `HEAVY_RAIN_SCENARIO`: uses an approved deterministic policy table.
- `CONTACT_DELAY`: user supplies start delay in seconds.

## Policy file

Create `data/fixtures/anomalies/policies.json` with schema and placeholders. Do not invent heavy-rain numerical coefficients. The corresponding policy is disabled until approved data is supplied. Test-only policies live under tests and are labeled test-only.

## Natural-language interface

```python
class AnomalyIntentParser(Protocol):
    async def parse(self, text: str, context: AnomalyContext) -> ParsedAnomalyIntent: ...
```

Implement `NotConfiguredGraniteAnomalyParser` and a deterministic `KeywordTestParser` used only in tests.

Parsed output may contain station reference, time interval, anomaly type, qualitative severity, explicit user-stated percentage, and missing fields. It must not contain a model-invented multiplier.

Flow:

```text
text → parsed intent → deterministic policy lookup → proposal → user confirmation → active anomaly
```

If required fields are missing, proposal status is `NEEDS_CLARIFICATION` with exact questions. No mutation occurs before confirmation.

## Acceptance

Test explicit outage, explicit 60% reduction, vague station/weather text, disabled policy, confirmation requirement, and Granite placeholder failure. Return the completion report and stop.

