import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AgccClient, resetSession } from './api'
import { AssumptionMark } from './DataStatus'
import { GlobeView, type GroundPoint, type SatelliteMarker, type StationMarker } from './GlobeView'
import { LiveWeather, type WeatherVisual } from './LiveWeather'
import { StationCatalogPicker } from './StationCatalogPicker'
import { useMissionStore, type Draft, type MissionMode } from './store'
import './weather.css'
import './assumptions.css'
import './styles.css'
import './task15.css'
import './v2.css'
import './runtime.css'
import './runtime-v3.css'
import './mission-controls.css'

const setupRoutes = ['/setup/orbit', '/setup/communications', '/setup/stations', '/setup/mission']
const routeLabel: Record<string, string> = { '/setup/orbit': 'Orbit', '/setup/communications': 'Communications', '/setup/stations': 'Stations', '/setup/mission': 'Mission' }
const sessionKeys: Record<MissionMode, string> = { prediction: 'agcc.session.prediction.v2', live: 'agcc.session.live.v2', branch: 'agcc.session.anomaly.v2' }
const clients: Record<MissionMode, AgccClient> = { prediction: new AgccClient('', sessionKeys.prediction), live: new AgccClient('', sessionKeys.live), branch: new AgccClient('', sessionKeys.branch) }
const navigate = (path: string, setPath: (path: string) => void) => { history.pushState({}, '', path); setPath(path) }
const Field = ({ label, children }: { label: string; children: React.ReactNode }) => <label className="field"><span>{label}</span>{children}</label>

type Opportunity = { pass_id: string; contact_id: string | null; station_id: string; station_name: string; start_at: string; end_at: string; volume_mb: number; classification: string; reason: string; planned_cost?: string | null; actual_volume_mb?: number; completed_at?: string | null; usable_capacity_mb?: number; average_effective_rate_mbps?: number; weather_precipitation_mm_per_hr?: number | null; weather_valid_from?: string | null; weather_quality?: string | null }
type Contact = { contact_id: string; station_id: string; station_name: string; start_at: string; end_at: string; rate_mbps: number; band: string; anomaly_multiplier: number; target_volume_mb: number; actual_volume_mb: number }
type BaselineIdentity = { snapshot_id: string | null; plan_id: string | null; created_at: string | null; weather_hash: string | null }
type SimulationState = { started: boolean; finished: boolean; sim_time: string; deadline_at: string; mission_start_at: string; mission_end_at: string | null; cost_used: string; committed_cost: string; remaining_budget: string; maximum_budget: string; cost_assumed: boolean; delivered_mb: number; remaining_mb: number; paused: boolean; speed: string; satellite: SatelliteMarker; current_contact: Contact | null; predicted_final_mb: number; predicted_shortfall_mb: number; confirmed_shortfall_mb: number; shortfall_status: 'clear' | 'provisional_monitoring' | 'confirmed_action_required'; required_mb: number; resolution_required: boolean; preflight: { capacity_policy: 'frozen' | 'live'; weather_frozen: boolean; ledger_allocated_mb: number; ledger_capacity_mb: number; feasible: boolean }; baseline: BaselineIdentity; plan: { plan_id: string; version: number; planned_completion_at: string | null; estimated_total_cost: string }; stations: StationMarker[]; opportunities: Opportunity[]; event_count: number }
type SimEvent = { event_id: string; sequence_number: number; event_type: string; sim_time: string; contact_id?: string; fragment_id?: string; delivered_volume_mb?: number; rate_mbps?: number; predicted_shortfall_mb?: number; planned_volume_mb?: number | null; planned_cost?: string | null; description: string; station_name?: string | null; source_station_name?: string | null; destination_station_name?: string | null; reroute_reason?: string | null }
type Runtime = { state: SimulationState; events: SimEvent[]; track: GroundPoint[] }
type AnomalyProposal = { proposal_id: string; status: string; rate_multiplier: number | null; clarification_questions: string[]; source_text: string; intent: { anomaly_type?: string; station_id?: string; qualitative_severity?: string; suggested_multiplier?: number; confidence?: number; cause?: string; starts_at?: string; ends_at?: string; assumptions?: string[] } }
type ReplanProposal = { proposal_id: string; predicted_shortfall_before_mb: number; predicted_shortfall_after_mb: number; approval_reasons: string[]; alternatives: { kind: string; calculated_value: string | number | string[] }[]; proposed_plan?: { contacts: { contact_id: string; station_id: string }[] } | null; diff: { added_contact_ids: string[]; removed_contact_ids: string[]; cost_delta: string } | null }
type Resolution = { reason: { summary: string; impact: string; action: string; tradeoff: string }; approval_prompt: string }
type WatsonStatus = { configured: boolean; status: string; reachable: boolean | null; endpoint?: string; model_id?: string; message?: string }
type PlanResult = { status: 'feasible' | 'no_feasible_plan_found'; validation_violations?: string[]; planned_volume_mb: number; required_volume_mb: number; estimated_total_cost: string; planned_completion_at?: string | null }
type InitializationFailure = { kind: 'constraint' | 'error'; message: string; plan?: PlanResult }
type TimelineInitializeResponse = { status: 'ready' | 'no_feasible_plan_found'; sessions?: Record<MissionMode, string>; states?: Record<MissionMode, SimulationState>; track?: GroundPoint[]; plan: PlanResult; baseline_at: string; baseline?: { snapshot_id: string; plan_id: string; created_at: string; baseline_at: string; weather_hash: string } }

const bandRanges: Record<string, [number, number, number]> = { S: [2, 4, 2.2], X: [8, 12, 9.6], Ka: [26.5, 40, 27.5] }
const localTime = (value: string) => new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })
const localClock = (value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone
function validateDraft(draft: Draft): string[] {
  const errors: string[] = []
  const range = bandRanges[draft.band]
  if (!range || draft.frequency < range[0] || draft.frequency > range[1]) errors.push(`${draft.band}-band requires ${range?.[0] ?? '?'}–${range?.[1] ?? '?'} GHz; ${draft.frequency} GHz is contradictory.`)
  if (draft.orbit.altitude_km < 200 || draft.orbit.altitude_km > 2000) errors.push('Circular LEO altitude must be between 200 and 2,000 km.')
  if (draft.orbit.inclination_deg < 0 || draft.orbit.inclination_deg > 180) errors.push('Inclination must be between 0° and 180°.')
  if (!(draft.rate > 0) || !(draft.protocolEfficiency > 0 && draft.protocolEfficiency <= 1)) errors.push('Rate must be positive and protocol efficiency must be in (0, 1].')
  if (!draft.stations.length) errors.push('Select at least one compatible ground station.')
  if (!(draft.required > 0) || !(draft.budget >= 0)) errors.push('Required data must be positive and budget cannot be negative.')
  if (new Date(draft.deadline) <= new Date(draft.orbit.epoch)) errors.push('Deadline must be after the orbit epoch/release time.')
  return errors
}

function OrbitManipulator({ draft, updateOrbit }: { draft: Draft; updateOrbit: (patch: Partial<Draft['orbit']>) => void }) {
  const preview = useMemo(() => Array.from({ length: 121 }, (_, index) => {
    const argument = index / 120 * Math.PI * 2
    const inclination = draft.orbit.inclination_deg * Math.PI / 180
    const latitude = Math.asin(Math.sin(inclination) * Math.sin(argument))
    const longitude = draft.orbit.raan_deg * Math.PI / 180 + Math.atan2(Math.cos(inclination) * Math.sin(argument), Math.cos(argument))
    return { latitude_deg: latitude * 180 / Math.PI, longitude_deg: ((longitude * 180 / Math.PI + 540) % 360) - 180 }
  }), [draft.orbit.inclination_deg, draft.orbit.raan_deg])
  const satellite = preview[Math.round((draft.orbit.phase_deg % 360) / 360 * 120)]
  return <div className="orbit-preview-stack"><GlobeView groundTrack={preview} satellite={{ ...satellite, altitude_km: draft.orbit.altitude_km }} orbitConfig={draft.orbit} onOrbitChange={updateOrbit}/></div>
}

function Setup({ path, setPath }: { path: string; setPath: (path: string) => void }) {
  const { draft, updateDraft, updateOrbit, applyDraft } = useMissionStore()
  const index = Math.max(0, setupRoutes.indexOf(path))
  const errors = validateDraft(draft)
  const visibleErrors = errors.filter((error) => {
    if (path === '/setup/orbit') return error.includes('altitude') || error.includes('Inclination') || error.includes('Deadline')
    if (path === '/setup/communications') return error.includes('-band') || error.includes('Rate')
    if (path === '/setup/stations') return error.includes('ground station')
    return true
  })
  const finish = () => { applyDraft(); navigate('/mission', setPath) }
  const chooseBand = (band: string) => updateDraft({ band, frequency: bandRanges[band][2], stations: [] })
  return <section className="setup-shell"><aside className="setup-progress"><span className="eyebrow">SCENARIO SETUP</span>{setupRoutes.map((route, i) => <button className={route === path ? 'active' : i < index ? 'done' : ''} onClick={() => navigate(route, setPath)} key={route}><b>{i + 1}</b>{routeLabel[route]}</button>)}</aside><section className="setup-stage"><div className="setup-copy"><span className="eyebrow">STEP {index + 1} OF 4</span><h2>{routeLabel[path] ?? 'Orbit'}</h2></div>
    {path === '/setup/orbit' && <div className="orbit-editor"><OrbitManipulator draft={draft} updateOrbit={updateOrbit}/><div className="field-grid"><Field label="Preset"><select value={draft.orbit.inclination_deg} onChange={(event) => updateOrbit({ inclination_deg: Number(event.target.value) })}><option value="53">Mid-inclination 550</option><option value="0">Equatorial 550</option><option value="90">Polar 550</option><option value="97.6">Retrograde demo 550</option></select></Field><Field label="Altitude (km)"><input type="number" min="200" max="2000" value={draft.orbit.altitude_km} onChange={(event) => updateOrbit({ altitude_km: +event.target.value })}/></Field><Field label="Inclination (degrees)"><input type="number" min="0" max="180" value={draft.orbit.inclination_deg} onChange={(event) => updateOrbit({ inclination_deg: +event.target.value })}/></Field><Field label="RAAN (degrees)"><input type="number" min="0" max="359.999" value={draft.orbit.raan_deg} onChange={(event) => updateOrbit({ raan_deg: +event.target.value })}/></Field><Field label="Phase (degrees)"><input type="number" min="0" max="359.999" value={draft.orbit.phase_deg} onChange={(event) => updateOrbit({ phase_deg: +event.target.value })}/></Field><Field label="Epoch UTC (initialized from this device)"><input value={draft.orbit.epoch} onChange={(event) => updateOrbit({ epoch: event.target.value })}/></Field></div></div>}
    {path === '/setup/communications' && <div className="field-grid"><Field label="Band"><select value={draft.band} onChange={(event) => chooseBand(event.target.value)}><option>X</option><option>S</option><option>Ka</option></select></Field><Field label="Exact carrier frequency (GHz)"><input className={errors.some((item) => item.includes('-band')) ? 'invalid' : ''} type="number" step=".1" value={draft.frequency} onChange={(event) => updateDraft({ frequency: +event.target.value })}/><small>{draft.band}: {bandRanges[draft.band][0]}–{bandRanges[draft.band][1]} GHz</small></Field><Field label="Maximum downlink rate (Mbps)"><input type="number" min=".01" value={draft.rate} onChange={(event) => updateDraft({ rate: +event.target.value })}/></Field><Field label="Polarization (required)"><select value={draft.polarization} required onChange={(event) => updateDraft({ polarization: event.target.value as Draft['polarization'] })}><option value="horizontal">Horizontal</option><option value="vertical">Vertical</option><option value="circular">Circular</option></select></Field><Field label="Protocol efficiency"><input type="number" step=".01" min=".01" max="1" value={draft.protocolEfficiency} onChange={(event) => updateDraft({ protocolEfficiency: +event.target.value })}/></Field></div>}
    {path === '/setup/stations' && <StationCatalogPicker/>}
    {path === '/setup/mission' && <div className="field-grid"><Field label="Required data (MB)"><input type="number" min=".01" value={draft.required} onChange={(event) => updateDraft({ required: +event.target.value })}/></Field><Field label="Hard deadline UTC"><input value={draft.deadline} onChange={(event) => updateDraft({ deadline: event.target.value })}/></Field><Field label="Maximum budget (USD)"><input type="number" min="0" value={draft.budget} onChange={(event) => updateDraft({ budget: +event.target.value })}/></Field><Field label="Planning preference"><select value={draft.preference} onChange={(event) => updateDraft({ preference: event.target.value })}><option value="fastest">Fastest</option><option value="lowest_cost">Lowest cost</option><option value="balanced">Balanced</option></select></Field></div>}
    {visibleErrors.length > 0 && <div className="validation-errors">{visibleErrors.map((error) => <p key={error}>{error}</p>)}</div>}<div className="setup-actions"><button disabled={index === 0} onClick={() => navigate(setupRoutes[index - 1], setPath)}>Back</button><button className="primary" disabled={index === setupRoutes.length - 1 && errors.length > 0} onClick={() => index === setupRoutes.length - 1 ? finish() : navigate(setupRoutes[index + 1], setPath)}>{index === setupRoutes.length - 1 ? 'Apply & create isolated timelines' : 'Continue'}</button></div></section></section>
}

function buildPayload(draft: Draft, mode: MissionMode, revision: number) {
  const suffix = `${mode}_${revision}`
  return { scenario: { scenario_id: `scenario_${suffix}`, name: `${mode} browser mission`, satellite_id: `sat_${suffix}`, station_ids: draft.stations, mission_id: `mission_${suffix}`, constraints: { maximum_budget: String(draft.budget), currency: 'USD', station_selection: { allow_all_eligible: false, authorized_station_ids: draft.stations }, planning_preference: draft.preference, allow_additional_contact_proposals: true } }, satellite: { satellite_id: `sat_${suffix}`, name: 'Custom satellite', orbit: draft.orbit, comms: { band: draft.band, carrier_frequency_ghz: draft.frequency, max_downlink_rate_mbps: draft.rate, protocol_efficiency: draft.protocolEfficiency, min_elevation_deg: 5, polarization: draft.polarization }, provenance: { source_type: 'manual', source_name: 'browser-session', fetched_at: new Date().toISOString(), assumption_fields: ['orbit', 'comms'] } }, mission: { mission_id: `mission_${suffix}`, name: 'Custom downlink', required_volume_mb: draft.required, release_at: draft.orbit.epoch, deadline_at: draft.deadline } }
}

function ModeTabs() { const { mode, setMode } = useMissionStore(); return <div className="mode-tabs" role="tablist">{([['prediction','Prediction'],['live','Live sources'],['branch','Anomalies']] as [MissionMode,string][]).map(([id,label]) => <button role="tab" aria-selected={mode === id} className={mode === id ? 'active' : ''} onClick={() => setMode(id)} key={id}>{label}</button>)}</div> }

function AnomalyChat({ runtime, refresh, resetToStart }: { runtime: Runtime; refresh: () => void; resetToStart: () => void }) {
  const client = clients.branch
  const [text, setText] = useState('')
  const [proposal, setProposal] = useState<AnomalyProposal | null>(null)
  const [replan, setReplan] = useState<ReplanProposal | null>(null)
  const [turns, setTurns] = useState<string[]>([])
  const [watson, setWatson] = useState<WatsonStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionBusy, setActionBusy] = useState<'confirm'|'replan'|'approve'|'reject'|null>(null)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [message, setMessage] = useState('Describe an anomaly in natural language. The configured LLM will normalize it and ask for missing details.')
  const stationName = (stationId?: string) => runtime.state.stations.find((item) => item.station_id === stationId)?.name ?? stationId ?? 'station needed'
  const probe = () => { setBusy(true); client.request<WatsonStatus>('/watsonx/status?probe=true').then(setWatson).catch((error) => setWatson({ configured: false, status: 'probe_failed', reachable: false, message: error.message })).finally(() => setBusy(false)) }
  useEffect(probe, [])
  const send = () => {
    const userTurn = `User: ${text.trim()}`
    const transcript = [...turns, userTurn]
    setTurns(transcript); setText(''); setBusy(true)
    client.request<AnomalyProposal>('/anomalies/chat', { method: 'POST', body: JSON.stringify({ text: transcript.join('\n') }) }).then((item) => {
      const interval = item.intent.ends_at
        ? ` from ${localTime(item.intent.starts_at ?? runtime.state.sim_time)} until ${localTime(item.intent.ends_at)}`
        : ` from ${localTime(item.intent.starts_at ?? runtime.state.sim_time)} onward`
      const confidence = item.intent.confidence == null
        ? ''
        : ` · confidence ${(item.intent.confidence * 100).toFixed(0)}%`
      const reply = item.clarification_questions.join(' ') || `Proposed ${item.intent.anomaly_type ?? 'anomaly'} at multiplier ${item.rate_multiplier ?? 'unresolved'}×${interval}${confidence}. Confirm to inject it.`
      setTurns((current) => [...current, `LLM: ${reply}`]); setProposal(item); setMessage(reply)
    }).catch((error) => { const reply = error.message ?? 'LLM anomaly parsing failed.'; setTurns((current) => [...current, `System: ${reply}`]); setMessage(reply) }).finally(() => setBusy(false))
  }
  const confirm = async () => {
    if (!proposal || actionBusy) return
    setActionBusy('confirm'); setMessage('Recording anomaly on the branch timeline…')
    try {
      await client.request(`/anomalies/confirm?proposal_id=${encodeURIComponent(proposal.proposal_id)}`, { method: 'POST' })
      setProposal((current) => current ? { ...current, status: 'confirmed' } : current)
      setMessage('Anomaly recorded at the branch simulation time. A forward replan is now available.')
      refresh()
    } catch (error) { setMessage((error as Error).message ?? 'Confirmation failed.') }
    finally { setActionBusy(null) }
  }
  const requestReplan = async () => {
    if (actionBusy) return
    setActionBusy('replan'); setMessage('Calculating an anomaly-adjusted forward plan. Please wait; this can take time for long missions…')
    try {
      const next = await client.request<ReplanProposal>('/replans', { method: 'POST', body: JSON.stringify({ reason: `LLM-normalized anomaly: ${proposal?.source_text ?? text}` }) })
      setReplan(next); setMessage('Forward replan calculated and validated. Review it before approval.')
    } catch (error) { setMessage((error as Error).message ?? 'Replan failed.') }
    finally { setActionBusy(null) }
  }
  const decide = async (decision: 'approve'|'reject') => {
    if (!replan || actionBusy) return
    setActionBusy(decision); setMessage(`${decision === 'approve' ? 'Approving and activating' : 'Rejecting'} the proposed branch plan…`)
    try {
      await client.request(`/replans/${replan.proposal_id}/${decision}`, { method: 'POST', body: JSON.stringify({ reason: `User ${decision}d proposal` }) })
      setMessage(`Replan ${decision}d on the anomaly branch.`)
      setReplan(null); refresh()
      if (decision === 'reject') setRejectOpen(true)
    } catch (error) { setMessage((error as Error).message ?? 'Decision failed.') }
    finally { setActionBusy(null) }
  }
  const openMissionSetup = () => {
    history.pushState({}, '', '/setup/mission')
    dispatchEvent(new PopStateEvent('popstate'))
    setRejectOpen(false)
  }
  const addedStations = replan?.proposed_plan?.contacts
    .filter((item) => replan.diff?.added_contact_ids.includes(item.contact_id))
    .map((item) => stationName(item.station_id)) ?? []
  return <>
    <section className="anomaly-workbench panel">
      <div className="watson-heading"><div><span className="eyebrow">SEPARATE ANOMALY TIMELINE · GROQ CHAT</span><h2>Describe what changed</h2><small>Branch time: {localTime(runtime.state.sim_time)}</small></div><div className="anomaly-heading-actions"><button type="button" onClick={resetToStart} disabled={Boolean(actionBusy)}>Reset branch to T=0</button><button className={`watson-status ${watson?.reachable ? 'ready' : 'error'}`} onClick={probe} disabled={busy || Boolean(actionBusy)}>{watson?.reachable ? `GROQ READY · ${watson.model_id}` : `${watson?.status?.replaceAll('_',' ') ?? 'TESTING GROQ'} · RETEST`}</button></div></div>
      {watson?.message && <p className="watson-error">{watson.message}</p>}
      <div className="chat-history">{turns.map((turn, index) => <p className={turn.startsWith('User:') ? 'user-turn' : turn.startsWith('LLM:') ? 'watson-turn' : 'system-turn'} key={`${index}-${turn}`}>{turn}</p>)}</div>
      <div className="anomaly-chat"><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder={`Example: ${runtime.state.stations.find((item) => item.classification === 'approved')?.name ?? 'the next station'} has severe link degradation`}/><button disabled={!text.trim() || busy || Boolean(actionBusy) || watson?.reachable === false} onClick={send}>{busy ? 'Contacting…' : 'Ask Groq'}</button></div>
      <p className="workflow-message">{message}</p>
      {actionBusy && <div className="action-progress" role="progressbar" aria-label={`${actionBusy} in progress`}><span/><b>{actionBusy === 'replan' ? 'Computing pass capacity and validating route…' : `${actionBusy} in progress…`}</b></div>}
      {proposal && <div className="proposal-card"><b>{proposal.status.replaceAll('_',' ')}</b><span>{proposal.intent.anomaly_type ?? 'unresolved'} · {stationName(proposal.intent.station_id)} · multiplier {proposal.rate_multiplier ?? 'needs clarification'}×<AssumptionMark reason="Groq proposed the effect; deterministic bounds validate the multiplier."/></span><button disabled={proposal.status !== 'pending' || Boolean(actionBusy)} onClick={confirm}>{actionBusy === 'confirm' ? 'Confirming…' : 'Confirm branch injection'}</button><button disabled={Boolean(actionBusy) || (proposal.status !== 'confirmed' && !runtime.events.some((event) => event.event_type === 'anomaly_detected'))} onClick={requestReplan}>{actionBusy === 'replan' ? 'Calculating…' : 'Calculate replan'}</button></div>}
      {replan && <div className="proposal-card"><b>Approval required</b><span>Added future contacts: {addedStations.join(', ') || 'No replacement contact'} · cost Δ {replan.diff?.cost_delta ?? 'not applicable'} · remaining shortfall {replan.predicted_shortfall_after_mb.toFixed(2)} MB</span><small>Approve explicitly authorizes the calculated plan cost. If needed, the branch budget ceiling will increase only to that total; the deadline remains hard.</small><button disabled={Boolean(actionBusy) || !replan.diff || replan.predicted_shortfall_after_mb > 1e-9} onClick={() => void decide('approve')}>{actionBusy === 'approve' ? 'Approving…' : 'Approve'}</button><button disabled={Boolean(actionBusy)} onClick={() => void decide('reject')}>{actionBusy === 'reject' ? 'Rejecting…' : 'Reject'}</button></div>}
    </section>
    {rejectOpen && <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><span className="eyebrow">REPLAN REJECTED</span><h2>Configure your own recovery path</h2><p>The anomaly branch remains unchanged. Open Mission Constraints to change the deadline, budget, planning preference, required volume, or selected stations, then apply the revised setup to calculate all three timelines again.</p><div className="modal-actions"><button onClick={() => setRejectOpen(false)}>Keep current branch</button><button onClick={openMissionSetup}>Edit mission constraints</button></div></div></div>}
  </>
}

function Mission() {
  const { appliedDraft, revision, mode, setMode } = useMissionStore()
  const [runtimes, setRuntimes] = useState<Partial<Record<MissionMode, Runtime>>>({})
  const [statuses, setStatuses] = useState<Partial<Record<MissionMode, string>>>({})
  const [failures, setFailures] = useState<Partial<Record<MissionMode, InitializationFailure>>>({})
  const [selectedStation, setSelectedStation] = useState<StationMarker | null>(null)
  const [resolution, setResolution] = useState<Resolution | null>(null)
  const [resolutionProposal, setResolutionProposal] = useState<ReplanProposal | null>(null)
  const [resolutionError, setResolutionError] = useState('')
  const [liveWeatherVisual, setLiveWeatherVisual] = useState<WeatherVisual | null>(null)
  const initializing = useRef(new Set<MissionMode>())
  const fetchingResolution = useRef<Record<string, boolean>>({})

  const refresh = useCallback(async (target: MissionMode) => {
    const client = clients[target]
    const [state, events] = await Promise.all([client.request<SimulationState>('/simulation/state'), client.request<SimEvent[]>('/simulation/events')])
    setRuntimes((current) => ({ ...current, [target]: { state, events, track: current[target]?.track ?? [] } }))
    if (state.resolution_required) {
      if (!fetchingResolution.current[target]) {
        fetchingResolution.current[target] = true
        client.request<Resolution>('/mission/resolution').then(setResolution).catch((error) => {
          setResolutionError(error instanceof Error ? error.message : 'Explanation request failed.')
          // Keep this target latched until the shortfall clears or the user retries.
          // This prevents a one-second polling loop from repeatedly charging the LLM.
        })
      }
    } else {
      fetchingResolution.current[target] = false
      setResolutionError('')
    }
  }, [])

  const initializeAll = useCallback(async (draft: Draft, currentRevision: number) => {
    if (initializing.current.has('prediction')) return
    initializing.current = new Set(['prediction', 'live', 'branch'])
    setStatuses({ prediction: 'Building shared authoritative baseline…', live: 'Waiting for shared baseline…', branch: 'Waiting for shared baseline…' })
    setFailures({})
    for (const target of ['prediction', 'live', 'branch'] as MissionMode[]) resetSession(clients[target])
    try {
      const payload = buildPayload(draft, 'prediction', currentRevision)
      const baselineAt = new Date(Math.max(Date.now(), Date.parse(draft.orbit.epoch))).toISOString()
      const result = await clients.prediction.request<TimelineInitializeResponse>('/timelines/initialize', {
        method: 'POST',
        body: JSON.stringify({ scenario: payload, baseline_at: baselineAt }),
      })
      if (result.status !== 'ready' || !result.sessions || !result.states || !result.track) {
        const detail = result.plan.validation_violations?.length
          ? result.plan.validation_violations.join(' · ')
          : `Only ${result.plan.planned_volume_mb.toFixed(2)} of ${result.plan.required_volume_mb.toFixed(2)} MB can be scheduled under the shared constraints.`
        const failure: InitializationFailure = { kind: 'constraint', message: detail, plan: result.plan }
        setFailures({ prediction: failure, live: failure, branch: failure })
        setStatuses({ prediction: 'Constraint analysis complete', live: 'Constraint analysis complete', branch: 'Constraint analysis complete' })
        return
      }
      for (const target of ['prediction', 'live', 'branch'] as MissionMode[]) {
        sessionStorage.setItem(sessionKeys[target], result.sessions[target])
      }
      const sharedTrack = result.track
      setRuntimes({
        prediction: { state: result.states.prediction, events: [], track: sharedTrack },
        live: { state: result.states.live, events: [], track: sharedTrack },
        branch: { state: result.states.branch, events: [], track: sharedTrack },
      })
      setStatuses({ prediction: 'Shared baseline ready', live: 'Shared baseline ready', branch: 'Shared baseline ready' })
    } catch (error) {
      const message = error instanceof Error ? error.message : String((error as { message?: string }).message ?? 'Atomic timeline setup failed')
      const failure: InitializationFailure = { kind: 'error', message }
      setFailures({ prediction: failure, live: failure, branch: failure })
      setStatuses({ prediction: message, live: message, branch: message })
    } finally {
      initializing.current.clear()
    }
  }, [])

  useEffect(() => {
    if (!appliedDraft) return
    initializing.current.clear(); setRuntimes({}); setFailures({}); setResolution(null); fetchingResolution.current = {}
    void initializeAll(appliedDraft, revision)
  }, [appliedDraft, revision, initializeAll])
  useEffect(() => { const timer = setInterval(() => { for (const target of ['prediction','live','branch'] as MissionMode[]) if (runtimes[target] && !runtimes[target]!.state.paused) void refresh(target) }, 1000); return () => clearInterval(timer) }, [runtimes, refresh])

  if (!appliedDraft) return <div className="live-unavailable"><h2>Create your custom satellite first</h2><p>The mission controller will remain mounted after creation, including while you return to Setup.</p></div>
  const runtime = runtimes[mode]
  if (!runtime) {
    const failure = failures[mode]
    if (failure) {
      const plan = failure.plan
      const transferable = plan?.planned_volume_mb ?? 0
      const required = plan?.required_volume_mb ?? appliedDraft.required
      const shortfall = Math.max(0, required - transferable)
      const ratio = transferable > 0 ? required / transferable : 2
      const suggestedBudget = Math.ceil(Math.max(appliedDraft.budget + 1, appliedDraft.budget * ratio * 1.05))
      const release = Date.parse(appliedDraft.orbit.epoch)
      const currentDeadline = Date.parse(appliedDraft.deadline)
      const suggestedDeadline = new Date(release + Math.max(24 * 3600_000, (currentDeadline - release) * ratio * 1.1)).toISOString()
      const applyConstraint = (patch: Partial<Draft>) => {
        useMissionStore.getState().updateDraft(patch)
        useMissionStore.getState().applyDraft()
      }
      return <><div className="mission-toolbar"><ModeTabs/></div><div className="constraint-backdrop"><section className="constraint-dialog" role="dialog" aria-modal="true"><span className="eyebrow">{mode.toUpperCase()} · CONSTRAINT DECISION REQUIRED</span><h2>{failure.kind === 'constraint' ? 'The requested mission is not feasible as configured' : 'Timeline initialization stopped'}</h2>{failure.kind === 'constraint' ? <><div className="constraint-numbers"><div><small>BEST-CASE TRANSFER</small><strong>{transferable.toFixed(2)} MB</strong></div><div><small>REQUESTED</small><strong>{required.toFixed(2)} MB</strong></div><div><small>UNRESOLVED</small><strong>{shortfall.toFixed(2)} MB</strong></div></div><p>The planner maximized executable transfer while respecting the present station set, deadline, and budget. It has finished calculating; nothing is still loading.</p><p className="constraint-detail">{failure.message}</p><div className="constraint-options"><button onClick={() => applyConstraint({ budget: suggestedBudget })}><b>Approve suggested budget</b><span>Raise ceiling from USD {appliedDraft.budget.toFixed(2)} to USD {suggestedBudget.toFixed(2)} and recalculate all timelines.</span></button><button onClick={() => applyConstraint({ deadline: suggestedDeadline })}><b>Approve suggested time</b><span>Extend deadline to {localTime(suggestedDeadline)} and recalculate all timelines.</span></button></div><small>Suggestions are conservative search starting points, not claims of guaranteed feasibility. The recalculation validates the chosen change against actual future passes and costs before a route is accepted.</small></> : <><p>{failure.message}</p><p>This is a terminal error, not a background loading operation.</p><button onClick={() => void initializeAll(appliedDraft, revision)}>Retry all timelines atomically</button></>}</section></div></>
    }
    return <><div className="mission-toolbar"><ModeTabs/></div><div className="loading-data-art"><div className="loading-content"><div className="spinner-rings"><div className="ring1"></div><div className="ring2"></div><div className="ring3"></div></div><h2>{statuses[mode] ?? 'INITIALIZING MISSION PROTOCOLS...'}</h2><p>Syncing orbital parameters and precomputing contact windows.</p><div className="loading-bar-container"><div className="loading-bar-fill"></div></div></div></div></>
  }
  const state = runtime.state
  const client = clients[mode]
  const setSpeed = (speed: string) => client.request<SimulationState>('/simulation/speed', { method: 'POST', body: JSON.stringify({ speed }) }).then((next) => setRuntimes((current) => ({ ...current, [mode]: { ...runtime, state: next } }))).catch((error) => setStatuses((current) => ({ ...current, [mode]: error.message ?? 'Speed change failed' })))
  const toggle = () => setSpeed(state.paused ? (state.speed === 'paused' ? '1x' : state.speed) : 'paused')
  const approved = state.opportunities.filter((item) => item.contact_id).sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))
  const route = [
    ...approved.filter((item) => state.current_contact?.contact_id === item.contact_id),
    ...approved.filter((item) => Date.parse(item.start_at) > Date.parse(state.sim_time)),
    ...approved.filter((item) => Date.parse(item.end_at) <= Date.parse(state.sim_time)).reverse(),
  ]
  const stationPasses = selectedStation ? approved.filter((item) => item.station_id === selectedStation.station_id) : []
  const stationContactIds = new Set(stationPasses.map((item) => item.contact_id).filter(Boolean))
  const stationEvents = selectedStation ? runtime.events.filter((event) => event.contact_id && stationContactIds.has(event.contact_id)) : []
  const reroutes = runtime.events.filter((event) => event.event_type === 'data_rerouted')
  const predictionRuntime = runtimes.prediction
  const predictionStationIds = new Set(
    predictionRuntime?.state.opportunities
      .filter((item) => item.contact_id)
      .map((item) => item.station_id) ?? [],
  )
  const liveAddedStations = mode === 'live'
    ? Array.from(new Map(
      approved
        .filter((item) => !predictionStationIds.has(item.station_id))
        .map((item) => [item.station_id, item.station_name]),
    ).values())
    : []
  const liveRemovedStations = mode === 'live' && predictionRuntime
    ? Array.from(new Map(
      predictionRuntime.state.opportunities
        .filter((item) => item.contact_id && !approved.some((live) => live.station_id === item.station_id))
        .map((item) => [item.station_id, item.station_name]),
    ).entries())
    : []
  const liveRouteReasons = mode === 'live'
    ? liveAddedStations.map((stationName) => {
      const stationOpportunities = state.opportunities.filter((item) => item.station_name === stationName)
      const selectedCapacity = stationOpportunities
        .filter((item) => item.contact_id)
        .reduce((sum, item) => sum + item.volume_mb, 0)
      const representative = stationOpportunities.find((item) => item.contact_id) ?? stationOpportunities[0]
      const rain = representative?.weather_precipitation_mm_per_hr
      const weatherText = rain == null
        ? 'weather value unavailable'
        : rain > 0.05
          ? `${rain.toFixed(2)} mm/h forecast rain was included in its attenuated capacity`
          : `${rain.toFixed(2)} mm/h forecast rain (effectively dry)`
      return `${stationName} was selected for ${selectedCapacity.toFixed(2)} MB of remaining executable capacity at ${representative?.average_effective_rate_mbps?.toFixed(2) ?? 'unknown'} Mbps; ${weatherText}.`
    })
    : []
  const liveRemovedReasons = liveRemovedStations.map(([stationId, stationName]) => {
    const candidates = state.opportunities.filter((item) => item.station_id === stationId)
    const usable = candidates.reduce((sum, item) => sum + (item.usable_capacity_mb ?? 0), 0)
    const rainiest = candidates.reduce<Opportunity | null>((current, item) =>
      current == null || (item.weather_precipitation_mm_per_hr ?? -1) > (current.weather_precipitation_mm_per_hr ?? -1) ? item : current, null)
    const rain = rainiest?.weather_precipitation_mm_per_hr
    const weatherReason = rain != null && rain > 0.05
      ? `Its refreshed forecast includes up to ${rain.toFixed(2)} mm/h rain.`
      : 'Rain was not the limiting factor in the refreshed forecast.'
    return `${stationName} was in Prediction but not selected by Live. Its remaining candidate opportunities offered ${usable.toFixed(2)} MB before overlap, budget, and deadline selection. ${weatherReason}`
  })
  const liveCostDelta = predictionRuntime
    ? Number(state.committed_cost) - Number(predictionRuntime.state.committed_cost)
    : 0
  const resetBranch = (simTime: string, deliveredMb: number) => clients.branch.request<SimulationState>('/simulation/fork', { method: 'POST', body: JSON.stringify({ sim_time: simTime, delivered_mb: deliveredMb }) }).then((next) => {
    setRuntimes((current) => ({ ...current, branch: { ...(current.branch ?? runtime), state: next, events: [] } }))
  })
  const branchFromPrediction = () => resetBranch(state.sim_time, state.delivered_mb).then(() => setMode('branch')).catch((error) => setStatuses((current) => ({ ...current, branch: error.message ?? 'Could not create anomaly branch.' })))
  const prepareResolution = () => { setResolutionError(''); client.request<ReplanProposal>('/replans', { method: 'POST', body: JSON.stringify({ reason: 'Predicted deadline shortfall requires a validated feasible resolution path' }) }).then((proposal) => { if (!proposal) throw new Error('No forward proposal could be produced from the current instant.'); setResolutionProposal(proposal) }).catch((error) => setResolutionError(error.message ?? 'Resolution calculation failed.')) }
  const approveResolution = () => resolutionProposal && client.request(`/replans/${resolutionProposal.proposal_id}/approve`, { method: 'POST', body: JSON.stringify({ reason: 'User approved the validated shortfall resolution' }) }).then(() => { setResolutionProposal(null); setResolution(null); fetchingResolution.current[mode] = false; void refresh(mode) }).catch((error) => setResolutionError(error.message ?? 'Approval failed due to a server error.'))

  const predictionBlocked = mode === 'prediction' && state.predicted_shortfall_mb > 1e-9
  const hasSharedBaseline = Boolean(state.baseline.snapshot_id && state.baseline.plan_id)
  const divergedFromBaseline = hasSharedBaseline && state.plan.plan_id !== state.baseline.plan_id
  return <><div className="mission-toolbar"><ModeTabs/><div className="clock-controls"><b>{mode === 'live' ? `LIVE SYSTEM TIME · ${localZone}` : 'INTERNAL SIMULATION TIME'}</b><span>{mode === 'live' ? localTime(state.sim_time) : new Date(state.sim_time).toISOString()}<AssumptionMark reason={mode === 'live' ? 'Live mode advances at 1× wall-clock time; displayed in this device’s time zone.' : 'Authoritative modeled time, not spacecraft telemetry.'}/></span>{mode !== 'live' && <><button disabled={predictionBlocked && state.paused} onClick={toggle}>{state.paused ? 'Start' : 'Pause'}</button>{['1x','10x','100x','1000x'].map((speed) => <button className={state.speed === speed ? 'active' : ''} onClick={() => setSpeed(speed)} key={speed}>{speed}</button>)}</>}{mode === 'live' && <span className="live-lock">REAL TIME · 1× · CONTINUES DURING APPROVAL</span>}</div></div>
    {hasSharedBaseline && <section className="baseline-identity panel"><b>SHARED AUTHORITATIVE BASELINE · {divergedFromBaseline ? 'DIVERGED WITH RECORDED PLAN VERSION' : 'MATCHED'}</b><span>Snapshot {state.baseline.snapshot_id} · initial plan {state.baseline.plan_id} · current plan {state.plan.plan_id}</span><small>Weather ledger {state.baseline.weather_hash?.slice(0, 16)}… · created {state.baseline.created_at ? localTime(state.baseline.created_at) : 'unknown'}. Prediction and Live began with identical contacts, allocations, costs, passes, and normalized weather.</small></section>}
    {mode === 'prediction' && <section className="branch-banner panel"><b>TEST A WHAT-IF FROM THIS EXACT MOMENT</b><span>The Prediction timeline remains untouched. The Anomaly timeline receives this timestamp and delivered-volume snapshot.</span><button type="button" onClick={branchFromPrediction}>Branch here & inject anomaly</button></section>}
    {mode === 'prediction' && <section className={`preflight-status panel ${state.preflight.feasible ? 'ready' : 'blocked'}`}><b>{state.preflight.feasible ? `FEASIBLE · ${state.preflight.ledger_allocated_mb.toFixed(2)} MB fully scheduled` : 'PREFLIGHT BLOCKED'}</b><span>Frozen forecast ledger · executable capacity {state.preflight.ledger_capacity_mb.toFixed(2)} MB · weather will not refresh during Prediction.</span></section>}
    {mode === 'branch' && <AnomalyChat runtime={runtime} refresh={() => void refresh('branch')} resetToStart={() => { if (appliedDraft) void resetBranch(appliedDraft.orbit.epoch, 0) }}/>} {mode === 'live' && <section className="live-banner panel"><b>LIVE EXECUTION TIMELINE</b><span>Open-Meteo forecasts feed the capacity model; contact-close redistribution moves undelivered fragments to upcoming approved contacts. New stations remain approval-gated.</span></section>}
    {mode === 'live' && (liveAddedStations.length > 0 || liveRemovedStations.length > 0) && <section className="live-plan-delta panel"><b>WHY THE LIVE ROUTE DIFFERS FROM PREDICTION</b><span>This comparison appeared when the independently calculated Live plan finished; it is not a route change caused by four seconds of weather fluctuation. Live starts at the current wall-clock instant and ranks its remaining passes using its own Open-Meteo capacity ledger. Prediction retains its earlier frozen ledger. The Anomaly timeline cannot affect either one.</span>{liveRouteReasons.map((reason) => <small key={reason}>{reason}</small>)}{liveRemovedReasons.map((reason) => <small key={reason}>{reason}</small>)}<small>Committed resource change versus Prediction: {liveCostDelta >= 0 ? '+' : ''}USD {liveCostDelta.toFixed(2)}{state.cost_assumed ? '*' : ''}.</small></section>}
    {mode === 'live' && state.shortfall_status === 'provisional_monitoring' && <section className="live-banner panel"><b>MONITORING PROVISIONAL RISK · {state.predicted_shortfall_mb.toFixed(2)} MB</b><span>The current contact is still executing. No approval is needed yet; the system will measure the final transfer and redistribute any confirmed loss when the contact closes.</span></section>}
    {state.resolution_required && <section className="approval-alert"><b>{state.finished ? `Mission reached its deadline with ${state.remaining_mb.toFixed(2)} MB unresolved.` : `${mode === 'prediction' ? 'Preflight' : 'Live forecast'} detects a ${state.predicted_shortfall_mb.toFixed(2)} MB shortfall; ${mode === 'prediction' ? 'Start is blocked' : 'the satellite continues in real time while a forward-only plan is prepared'}.`}</b><span>{resolution?.reason.summary ?? 'WatsonX is preparing a grounded explanation.'} {resolution?.reason.impact}</span><span>{resolution?.approval_prompt ?? 'A constraint change or specific forward plan requires your approval.'}</span>{resolutionError && <span className="resolution-error">Resolution calculation failed: {resolutionError}</span>}{!resolutionProposal && <button onClick={prepareResolution}>Calculate specific resolution</button>}{resolutionProposal && <div className="validated-resolution"><b>Validated recommendation from {localTime(state.sim_time)}</b><span>Shortfall: {resolutionProposal.predicted_shortfall_before_mb.toFixed(2)} → {resolutionProposal.predicted_shortfall_after_mb.toFixed(2)} MB · cost Δ {resolutionProposal.diff?.cost_delta ?? 'not applicable'}</span>{resolutionProposal.approval_reasons.map((reason) => <small key={reason}>{reason}</small>)}{resolutionProposal.alternatives.map((alternative) => <small key={alternative.kind}>{alternative.kind.replaceAll('_',' ')}: {String(alternative.calculated_value)}</small>)}<button disabled={resolutionProposal.predicted_shortfall_after_mb > 1e-9 || !resolutionProposal.diff} onClick={approveResolution}>Approve recommended plan</button>{resolutionProposal.predicted_shortfall_after_mb > 1e-9 && <small>No plan is presented as successful because the calculated shortfall remains non-zero.</small>}</div>}</section>}
    <section className="v2-dashboard"><section className="earth-panel panel"><GlobeView groundTrack={runtime.track} stations={state.stations} satellite={state.satellite} activeStationId={state.current_contact?.station_id} weather={mode === 'live' ? liveWeatherVisual : null} onStationSelect={setSelectedStation}/><div className="earth-caption"><span className="eyebrow">MODELED CUSTOM SATELLITE · {mode.toUpperCase()} TIMELINE</span><h2>{state.satellite.latitude_deg.toFixed(2)}°, {state.satellite.longitude_deg.toFixed(2)}°</h2><small><span className="legend-item active">active</span><span className="legend-item planned">planned</span><span className="legend-item candidate">candidate</span><span className="legend-item unused">unused</span></small></div>{state.current_contact && <div className="satellite-hud"><div className="hud-chip"><span>ALT</span><b>{state.satellite.altitude_km?.toFixed(0) ?? '—'} km</b></div><div className="hud-chip"><span>BAND</span><b>{state.current_contact.band}</b></div><div className="hud-chip"><span>LINK</span><b>{state.current_contact.rate_mbps.toFixed(1)} Mbps</b></div></div>}{selectedStation && <div className="station-popover"><button onClick={() => setSelectedStation(null)}>×</button><b>{selectedStation.name}</b><span>{selectedStation.classification} · {selectedStation.latitude_deg.toFixed(3)}°, {selectedStation.longitude_deg.toFixed(3)}°</span>{stationPasses.length ? stationPasses.map((item) => { const completed = Date.parse(item.end_at) <= Date.parse(state.sim_time); const actual = stationEvents.filter((event) => event.contact_id === item.contact_id).reduce((sum, event) => sum + (event.delivered_volume_mb ?? 0), 0); return <small key={item.pass_id}>{completed ? 'PAST' : 'PLANNED'} · {new Date(item.start_at).toLocaleString()} → {new Date(item.end_at).toLocaleTimeString()} · target {item.volume_mb.toFixed(2)} MB · transferred {actual.toFixed(2)} MB</small> }) : <small>No planned or past contact in this timeline.</small>}</div>}</section>
      <aside className="target-panel panel"><span className="eyebrow">MISSION TARGET · HARD DEADLINE {localTime(state.deadline_at)}</span><h2>Downlink completion</h2><div className="current-link"><span>Live modeled rate</span><strong>{(state.current_contact?.rate_mbps ?? 0).toFixed(2)} Mbps</strong><small>{state.current_contact ? `${state.current_contact.station_name} · target ${state.current_contact.target_volume_mb.toFixed(2)} MB · actual ${state.current_contact.actual_volume_mb.toFixed(2)} MB` : 'No active ground-station contact'}</small></div><dl>{[['Required',`${state.required_mb.toFixed(2)} MB`],['Delivered',`${state.delivered_mb.toFixed(2)} MB`],['Remaining',`${state.remaining_mb.toFixed(2)} MB`],['Predicted final',`${state.predicted_final_mb.toFixed(2)} MB`],['Predicted shortfall',`${state.predicted_shortfall_mb.toFixed(2)} MB`],['Incurred cost',`USD ${Number(state.cost_used).toFixed(2)}${state.cost_assumed ? '*' : ''}`],['Committed cost',`USD ${Number(state.committed_cost).toFixed(2)}${state.cost_assumed ? '*' : ''}`],['Remaining budget',`USD ${Number(state.remaining_budget).toFixed(2)}`],['Maximum budget',`USD ${Number(state.maximum_budget).toFixed(2)}`],['Mission start',localTime(state.mission_start_at)],['Mission end',state.mission_end_at ? localTime(state.mission_end_at) : 'Not calculated']].map(([key,value]) => <div key={key}><dt>{key}<AssumptionMark reason={key.includes('cost') && state.cost_assumed ? 'At least one selected station uses deterministic simulated pricing because provider pricing was unavailable.' : 'Backend-calculated simulation value.'}/></dt><dd>{value}</dd></div>)}</dl>{mode === 'live' && <LiveWeather client={client} simulationTime={state.sim_time} activeStationId={state.current_contact?.station_id} onActiveWeather={setLiveWeatherVisual}/>}</aside>
      <section className="passes-panel panel"><div className="panel-heading"><div><span className="eyebrow">SEQUENTIAL APPROVED ROUTE · {localZone}</span><h2>Planner decisions · active/next contact first</h2></div><span>{approved.length} planned contacts</span></div><div className="pass-list route-list">{route.map((item, index) => { const completed = Boolean(item.completed_at) || Date.parse(item.end_at) <= Date.parse(state.sim_time); const active = state.current_contact?.contact_id === item.contact_id; const actual = (active ? state.current_contact?.actual_volume_mb ?? 0 : item.actual_volume_mb ?? 0); const delta = completed ? actual - item.volume_mb : 0; const deltaStr = completed ? (delta > 0.01 ? ` (Gain: +${delta.toFixed(1)} MB)` : delta < -0.01 ? ` (Loss: ${delta.toFixed(1)} MB)` : '') : ''; return <button className={`pass-row approved ${active ? 'active' : completed ? 'complete' : 'upcoming'}`} onClick={() => setSelectedStation(state.stations.find((station) => station.station_id === item.station_id) ?? null)} key={item.pass_id}><time>{index + 1} · {localTime(item.start_at)} → {localClock(item.end_at)}</time><b>{item.station_name}</b><span>planned {item.volume_mb.toFixed(1)} MB · actual {actual.toFixed(1)} MB{deltaStr}</span><i>{active ? 'IN PROGRESS' : completed ? 'COMPLETED' : 'UPCOMING'}</i></button> })}</div><div className="decision-detail"><span className="eyebrow">TRANSFER LOG SUMMARY</span><h3>{approved.filter((item) => Boolean(item.completed_at) || Date.parse(item.end_at) <= Date.parse(state.sim_time)).length} completed · {approved.filter((item) => Date.parse(item.start_at) > Date.parse(state.sim_time)).length} upcoming</h3><p>Planned {approved.reduce((sum, item) => sum + item.volume_mb, 0).toFixed(2)} MB · actually transferred {approved.reduce((sum, item) => sum + (item.actual_volume_mb ?? 0), 0).toFixed(2)} MB.</p></div></section>
      <aside className="event-panel panel"><div className="panel-heading"><div><span className="eyebrow">AUTHORITATIVE EVENT LOG · {mode === 'live' ? localZone : 'SIMULATION UTC'}</span><h2>Transfer lifecycle and algorithm decisions</h2></div></div>{reroutes.length > 0 && <section className="reroute-summary"><b>{mode === 'live' ? 'Real-time' : 'Deterministic'} redistribution decisions</b>{reroutes.slice().reverse().map((event) => <p key={`reroute-${event.event_id}`}><time>{localClock(event.sim_time)}</time><span>{event.source_station_name ?? 'Previous station'} under-delivered; {(event.delivered_volume_mb ?? 0).toFixed(3)} MB reassigned to {event.destination_station_name ?? 'the next approved station'} because it had spare future capacity before the deadline. {event.reroute_reason}</span></p>)}</section>}      <div className="event-list">{runtime.events.slice().reverse().map((event) => { const station = event.station_name ?? 'ground station'; const planned = event.planned_volume_mb == null ? '' : `; planned ${event.planned_volume_mb.toFixed(3)} MB`; const delta = event.planned_volume_mb != null ? (event.delivered_volume_mb ?? 0) - event.planned_volume_mb : 0; const deltaEl = event.planned_volume_mb != null ? (delta > 0.01 ? <span className="delta-gain"> +{delta.toFixed(2)} MB</span> : delta < -0.01 ? <span className="delta-loss"> {delta.toFixed(2)} MB</span> : null) : null; const transferLabel = event.event_type === 'contact_started' ? `Transfer started · ${station}${planned}` : event.event_type === 'contact_ended' ? `Transfer ended · ${station}${planned}; actual ${(event.delivered_volume_mb ?? 0).toFixed(3)} MB` : event.event_type === 'fragment_started' ? `Segment started · ${station}` : event.event_type === 'fragment_partial' ? `${(event.delivered_volume_mb ?? 0).toFixed(3)} MB via ${station}` : event.event_type === 'fragment_delivered' ? `Segment complete · ${(event.delivered_volume_mb ?? 0).toFixed(3)} MB via ${station}` : event.event_type === 'rate_updated' ? `Link rate → ${(event.rate_mbps ?? 0).toFixed(2)} Mbps · ${station}` : event.event_type === 'shortfall_predicted' ? `Shortfall: ${(event.predicted_shortfall_mb ?? 0).toFixed(3)} MB ungainful` : event.event_type === 'data_rerouted' ? `Rerouted ${(event.delivered_volume_mb ?? 0).toFixed(3)} MB from ${event.source_station_name ?? 'source'} → ${event.destination_station_name ?? 'destination'}. ${event.reroute_reason ?? ''}` : `${event.station_name ? `${event.station_name}: ` : ''}${event.description || event.event_type.replaceAll('_',' ')}`; return <div className="event-row" key={event.event_id}><time>{mode === 'live' ? localClock(event.sim_time) : new Date(event.sim_time).toISOString().slice(11,19)}</time><i/><p><strong>{event.event_type.replaceAll('_',' ')}</strong><span>{transferLabel}{deltaEl}</span></p></div> })}</div></aside>
    </section></>
}

export default function App() {
  const initialPath = location.pathname === '/' || location.pathname === '/mission' ? '/setup/orbit' : location.pathname
  const [path, setPath] = useState(initialPath)
  const [connection, setConnection] = useState('CONNECTING')
  const { appliedDraft } = useMissionStore()
  useEffect(() => { if (location.pathname !== initialPath) history.replaceState({}, '', initialPath); const pop = () => setPath(location.pathname); addEventListener('popstate', pop); fetch('/api/v1/health').then((response) => { if (!response.ok) throw new Error('Backend unavailable'); setConnection('BACKEND READY') }).catch(() => setConnection('BACKEND OFFLINE')); return () => removeEventListener('popstate', pop) }, [])
  const setupVisible = path.startsWith('/setup/')
  return <main className="app-shell"><header className="topbar"><div className="brand-lockup"><div className="brand-mark">A</div><div><span className="eyebrow">AUTONOMOUS GROUND CONTACT CONTROL</span><h1>Custom Satellite Downlink</h1></div></div><nav className="main-nav"><button disabled={!appliedDraft} className={path === '/mission' ? 'active' : ''} onClick={() => navigate('/mission', setPath)}>Mission</button><button className={setupVisible ? 'active' : ''} onClick={() => navigate('/setup/orbit', setPath)}>Setup</button></nav><div className="mission-status"><span className={`status-chip ${connection === 'BACKEND READY' ? 'live' : ''}`}><i/>{connection}</span></div></header><div hidden={!setupVisible}><Setup path={path} setPath={setPath}/></div><div hidden={setupVisible}><Mission/></div></main>
}
