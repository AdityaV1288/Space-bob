import { vi } from 'vitest'

export const simulationState = {
  started: true, finished: false, sim_time: '2026-08-21T00:00:00Z', deadline_at: '2026-08-22T00:00:00Z',
  mission_start_at: '2026-08-21T00:00:00Z', mission_end_at: null, cost_used: '0',
  delivered_mb: 0, remaining_mb: 3000, paused: true, speed: 'paused',
  satellite: { satellite_id: 'sat_browser01', latitude_deg: 1, longitude_deg: 2, altitude_km: 550, modeled: true },
  current_contact: null, predicted_final_mb: 3000, predicted_shortfall_mb: 0,
  confirmed_shortfall_mb: 0, shortfall_status: 'clear', committed_cost: '0', remaining_budget: '1000', maximum_budget: '1000', cost_assumed: true,
  required_mb: 3000, resolution_required: false, event_count: 1,
  preflight: { capacity_policy: 'frozen', weather_frozen: true, ledger_allocated_mb: 3000, ledger_capacity_mb: 3200, feasible: true },
  baseline: { snapshot_id: 'baseline_test', plan_id: 'plan_browser0001', created_at: '2026-08-21T00:00:00Z', weather_hash: 'abcdef0123456789' },
  plan: { plan_id: 'plan_browser0001', version: 1, planned_completion_at: '2026-08-21T06:00:00Z', estimated_total_cost: '100' },
  stations: [{ station_id: 'station_demo_southafrica', name: 'South Africa', latitude_deg: -30, longitude_deg: 20, classification: 'approved', assumed_fields: ['latitude_deg'] }],
  opportunities: [{ pass_id: 'pass_0001', contact_id: 'contact_0001', station_id: 'station_demo_southafrica', station_name: 'South Africa', start_at: '2026-08-21T01:00:00Z', end_at: '2026-08-21T01:10:00Z', volume_mb: 3000, classification: 'approved', reason: 'Included in approved plan' }],
}

export const mockApiFetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  let payload: unknown = {}
  if (url.endsWith('/health')) payload = { status: 'ready' }
  else if (url.endsWith('/timelines/initialize')) payload = { status: 'ready', sessions: { prediction: 'session_prediction', live: 'session_live', branch: 'session_branch' }, states: { prediction: simulationState, live: { ...simulationState, paused: false, speed: '1x', preflight: { ...simulationState.preflight, capacity_policy: 'live', weather_frozen: false } }, branch: simulationState }, track: [], plan: { status: 'feasible', planned_volume_mb: 3000, required_volume_mb: 3000, estimated_total_cost: '100' }, baseline_at: simulationState.sim_time }
  else if (url.endsWith('/sessions')) payload = { session_id: 'session_test' }
  else if (url.endsWith('/catalog/stations')) payload = { stations: [{ station_id: 'station_demo_southafrica', supported_bands: ['X'] }] }
  else if (url.endsWith('/orbit/ground-track')) payload = []
  else if (url.endsWith('/plan')) payload = { status: 'feasible', planned_volume_mb: 3000, required_volume_mb: 3000, validation_violations: [] }
  else if (url.endsWith('/simulation/start') || url.endsWith('/simulation/state') || url.endsWith('/simulation/speed')) payload = simulationState
  else if (url.endsWith('/simulation/events')) payload = []
  return { ok: true, json: async () => payload } as Response
})
