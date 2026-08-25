# AGCC controlled build packet

This directory is the sole implementation authority for the fresh AGCC repository.

## How to use it

1. Give Bob `AGCC_00_GOVERNING_CONTEXT.md` at the start of every new Bob session.
2. Give Bob exactly one numbered task file.
3. Bob implements only that task, runs its checks, returns the required completion report, and stops.
4. A human reviews the report and code. Do not continue until the task acceptance criteria pass.
5. If two specifications conflict, the lower-numbered governing file wins unless a later file explicitly says it amends a named rule.

## Execution order

1. `AGCC_01_REPOSITORY_BOOTSTRAP.md`
2. `AGCC_02_DOMAIN_CONTRACTS.md`
3. `AGCC_03_CUSTOM_ORBIT_INPUT.md`
4. `AGCC_04_ORBIT_PROPAGATION.md`
5. `AGCC_05_STATION_CATALOG.md`
6. `AGCC_06_PASS_WINDOWS.md`
7. `AGCC_07_ENVIRONMENT_ADAPTERS.md`
8. `AGCC_08_CAPACITY_MODEL.md`
9. `AGCC_09_MISSION_AND_FEASIBILITY.md`
10. `AGCC_10_CONTACT_PLANNER.md`
11. `AGCC_11_FRAGMENT_DISPATCH.md`
12. `AGCC_12_SIMULATION_ENGINE.md`
13. `AGCC_13_ANOMALIES.md`
14. `AGCC_14_REPLANNING_AND_APPROVALS.md`
15. `AGCC_15_BACKEND_API.md`
16. `AGCC_16_FRONTEND_FOUNDATION.md`
17. `AGCC_17_VISUALIZATION_AND_MODES.md`
18. `AGCC_18_GRANITE_EXPLANATIONS.md`
19. `AGCC_19_END_TO_END_VERIFICATION.md`

Do not upload all task files to Bob simultaneously.

