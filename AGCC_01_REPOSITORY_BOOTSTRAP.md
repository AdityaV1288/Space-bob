# Task 01 — Repository bootstrap

## Objective

Create the empty monorepo foundation, tool configuration, and verification commands. Do not implement domain logic, APIs, or UI screens.

## Required tree

```text
agcc/
  backend/
    pyproject.toml
    src/agcc/__init__.py
    tests/__init__.py
  frontend/
    package.json
    tsconfig.json
    vite.config.ts
    index.html
    src/main.tsx
    src/App.tsx
  data/
    catalogs/.gitkeep
    fixtures/.gitkeep
    recorded/.gitkeep
  docs/.gitkeep
  .gitignore
  README.md
```

## Backend configuration

Set Python requirement to `>=3.12,<3.13`. Runtime dependencies: `pydantic>=2.8,<3`, `fastapi`, `uvicorn`, `skyfield`, `httpx`. Development dependencies: `pytest`, `pytest-cov`, `ruff`, `mypy`.

Configure Ruff for line length 100 and Python 3.12. Configure MyPy strict mode for `src/agcc`. Configure Pytest to find `backend/tests`.

## Frontend configuration

Dependencies: React 18, React DOM, Zustand, Three, `@react-three/fiber`, `@react-three/drei`. Development dependencies: TypeScript, Vite, React Vite plugin, Vitest, Testing Library, ESLint.

`App.tsx` must render only `AGCC bootstrap ready`. No navigation or product UI.

## Root README

Document commands for backend install/test/lint/type-check and frontend install/test/build. Do not claim the application is implemented.

## Exclusions

- No Docker.
- No CI workflow.
- No API routes.
- No environment variables.
- No domain models.
- No production deployment.

## Acceptance

Run:

```text
cd backend && pytest
cd backend && ruff check .
cd backend && mypy src/agcc
cd frontend && npm test -- --run
cd frontend && npm run build
```

All commands must succeed. Return the governing completion report and stop.

