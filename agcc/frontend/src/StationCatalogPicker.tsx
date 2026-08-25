import { useEffect, useMemo, useState } from 'react'
import { AgccClient, ensureSession } from './api'
import { AssumptionMark } from './DataStatus'
import { useMissionStore } from './store'

type Station = { station_id: string; name: string; provider_id: string; supported_bands: string[] | null; max_downlink_rate_mbps: number | null; cost_per_minute: number; currency: string; field_provenance: { sources: Record<string, string>; assumptions: string[] } }
type Catalog = { catalog_id: string; catalog_version: string; stations: Station[] }
const stationClient = new AgccClient()

export function StationCatalogPicker() {
  const { draft, updateDraft } = useMissionStore()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [band, setBand] = useState(draft.band)
  const [eligibleOnly, setEligibleOnly] = useState(true)
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    ensureSession(stationClient).then(() => stationClient.request<Catalog>('/catalog/stations')).then((loaded) => {
      setCatalog(loaded)
      const valid = draft.stations.filter((id) => loaded.stations.some((station) => station.station_id === id))
      if (valid.length === 0) {
        const compatible = loaded.stations.filter((station) => station.supported_bands?.includes(draft.band))
        updateDraft({ stations: compatible.slice(0, 3).map((station) => station.station_id) })
      }
    }).catch(() => setError('Station catalogue could not be loaded.'))
  }, [])
  const providers = useMemo(() => [...new Set(catalog?.stations.map((station) => station.provider_id) ?? [])].sort(), [catalog])
  const visible = useMemo(() => catalog?.stations.filter((station) => {
    const compatible = station.supported_bands?.includes(draft.band) ?? false
    const matchesBand = band === 'all' || station.supported_bands?.includes(band)
    const matchesProvider = provider === 'all' || station.provider_id === provider
    const needle = query.trim().toLowerCase()
    const matchesQuery = !needle || `${station.name} ${station.station_id} ${station.provider_id}`.toLowerCase().includes(needle)
    return (!eligibleOnly || compatible) && matchesBand && matchesProvider && matchesQuery
  }) ?? [], [catalog, draft.band, band, provider, query, eligibleOnly])
  const displayed = expanded ? visible : visible.slice(0, 20)
  const filteredCompatibleIds = visible
    .filter((station) => station.supported_bands?.includes(draft.band) ?? false)
    .map((station) => station.station_id)
  const allFilteredSelected = filteredCompatibleIds.length > 0 && filteredCompatibleIds.every((id) => draft.stations.includes(id))
  if (error) return <div className="catalog-error">{error}</div>
  if (!catalog) return <p>Loading validated station catalogue…</p>
  return <div><div className="station-filters"><input aria-label="Search stations" placeholder="Search station, ID, or provider" value={query} onChange={(event) => { setQuery(event.target.value); setExpanded(false) }}/><select aria-label="Provider filter" value={provider} onChange={(event) => { setProvider(event.target.value); setExpanded(false) }}><option value="all">All providers</option>{providers.map((item) => <option key={item}>{item}</option>)}</select><select aria-label="Band filter" value={band} onChange={(event) => { setBand(event.target.value); setExpanded(false) }}><option value="all">All bands</option>{['S','X','Ka'].map((item) => <option key={item}>{item}</option>)}</select><button type="button" className={eligibleOnly ? 'active' : ''} onClick={() => { setEligibleOnly((value) => !value); setExpanded(false) }}>Planner eligible: {eligibleOnly ? 'on' : 'off'}</button><button type="button" disabled={allFilteredSelected || filteredCompatibleIds.length === 0} onClick={() => updateDraft({ stations: [...new Set([...draft.stations, ...filteredCompatibleIds])] })}>Select all filtered ({filteredCompatibleIds.length})</button><button type="button" disabled={draft.stations.length === 0} onClick={() => updateDraft({ stations: [] })}>Clear selection</button><span>{draft.stations.length} selected · {displayed.length} of {visible.length} shown · {catalog.catalog_version}</span></div>
    <p className="catalog-disclosure"><AssumptionMark reason="The supplied hybrid catalogue has no authoritative field citations."/> Real provider/place labels are retained, but starred properties are simulation assumptions.</p>
    {draft.stations.length === 0 && <p className="catalog-error">Select at least one compatible station before creating the scenario.</p>}
    <div className="station-card-grid">{displayed.map((station) => { const assumed = new Set(station.field_provenance.assumptions); const selected = draft.stations.includes(station.station_id); const compatible = station.supported_bands?.includes(draft.band) ?? false; const mark = (field: string) => assumed.has(field) ? <AssumptionMark reason={`${field} is unverified in the supplied catalogue.`}/> : null; return <label className={`${selected ? 'selected' : ''} ${compatible ? '' : 'incompatible'}`} key={station.station_id}><input type="checkbox" disabled={!compatible} checked={selected} onChange={(event) => updateDraft({ stations: event.target.checked ? [...draft.stations, station.station_id] : draft.stations.filter((id) => id !== station.station_id) })}/><b>{station.name}{mark('name')}</b><span>{station.provider_id}{mark('provider_id')}</span><small>{station.supported_bands?.join(' / ') || 'Bands unknown'}{mark('supported_bands')}</small><small>{station.max_downlink_rate_mbps?.toFixed(1) ?? '—'} Mbps{mark('max_downlink_rate_mbps')} · {station.currency} {station.cost_per_minute.toFixed(1)}/min{mark('cost_per_minute')}</small>{!compatible && <em>Incompatible with {draft.band}-band</em>}</label> })}</div>
    {visible.length > 20 && <button className="catalog-fold" type="button" onClick={() => setExpanded((value) => !value)}>{expanded ? 'Show less' : `See more (${visible.length - 20} remaining)`}</button>}
  </div>
}
