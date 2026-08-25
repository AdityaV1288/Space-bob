# AGCC — Autonomous Ground Contact Control

Interactive custom-satellite contact-planning and simulation prototype. It does
not control spacecraft, book real stations, or claim certified RF performance.

## Backend

Run from `backend/`:

```sh
pip install -e ".[dev]"
pytest
ruff check .
mypy src/agcc
python scripts/verify_all.py --benchmark
uvicorn agcc.api.app:app --reload --port 8000
```

The primary interface is `/api/v1`. Browser-tab sessions are held in memory and
sent through `X-AGCC-Session`. The optional explanation/anomaly provider in this
experimental copy is Groq. Configure it in the backend PowerShell window:

```powershell
$env:GROQ_API_KEY="your-groq-api-key"
$env:GROQ_MODEL_ID="llama-3.3-70b-versatile"
```

Never paste keys into source files or commit them. Deterministic explanation
fallbacks remain available when Groq is not configured or unavailable.

Live ground weather uses Open-Meteo when its endpoint is set before the backend
starts. No API key is required for the free prototype endpoint:

```powershell
$env:AGCC_WEATHER_API_URL="https://api.open-meteo.com/v1/forecast"
```

The adapter requests UTC hourly `rain` and `showers` using each station's
latitude, longitude, and assumed elevation. Their sum is normalized as the
mean liquid-rain rate in mm/h for that one-hour interval. It also normalizes
temperature, humidity, cloud, and wind; caches responses for five minutes; and
records a raw-payload hash. Missing or out-of-range forecasts are reported as
unavailable rather than silently treated as clear weather. Open-Meteo forecast
data requires attribution; the free endpoint is for non-commercial prototyping
and has no uptime guarantee.
Rain attenuation uses the Level-A ITU-R P.618/P.838/P.839 model with the custom
satellite's required polarization. This remains a planning estimate, not RF
telemetry or certified link-budget performance.

NOAA planetary K-index context is optional and does not alter capacity:

```powershell
$env:AGCC_SPACE_WEATHER_API_URL="https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
```

The converted hybrid station catalogue is opt-in so deterministic verification
continues to use the controlled fictional fixture. Enable it before starting
the backend:

```powershell
$env:AGCC_STATION_CATALOG_PATH="C:\Users\amita\Documents\ChatGPT\spacebobtry2\spacebobv3\agcc\data\catalogs\stations.hybrid.json"
```

The hybrid catalogue retains supplied organization/place labels, but every
uncited location, capability, timing, price, and availability field remains in
`field_provenance.assumptions`. The UI renders a hoverable `*` beside assumed
or simulated values. Do not present it as provider-verified operational data.

## Frontend

Run from `frontend/`:

```sh
npm install
npm test -- --run
npm run build
npm run dev
```

The Vite development server proxies `/api` to the backend on port 8000. The UI
uses `sessionStorage`, never `localStorage`. The backend simulation clock is the
authority for propagated position, contacts, rates, delivered volume, station
classification, anomalies, and replanning proposals shown in the UI.

Controlled verification scenarios and hashes are stored under
`data/fixtures/scenarios/`. Human review remains required before demo acceptance.
