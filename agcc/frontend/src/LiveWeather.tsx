import { useEffect, useMemo, useState } from 'react'
import { AgccClient } from './api'
import { AssumptionMark } from './DataStatus'

type WeatherSnapshot = {
  snapshot_id: string; station_id: string; valid_from: string
  precipitation_mm_per_hr: number; temperature_c: number
  relative_humidity_pct: number; cloud_cover_pct: number; wind_speed_mps: number
  source_quality: string; provenance: { source_name: string; fetched_at: string }
}
export type WeatherVisual = { kind: 'clear' | 'cloud' | 'rain'; intensity: number }

export function LiveWeather({ client = new AgccClient(), simulationTime, activeStationId, onActiveWeather }: { client?: AgccClient; simulationTime?: string; activeStationId?: string; onActiveWeather?: (weather: WeatherVisual | null) => void }) {
  const [data, setData] = useState<WeatherSnapshot[]>([])
  const [error, setError] = useState('')
  const weatherHour = simulationTime?.slice(0, 13)
  const plannedWeather = useMemo(() => {
    const reference = simulationTime ? Date.parse(simulationTime) : Date.now()
    const closest = new Map<string, WeatherSnapshot>()
    for (const item of data) {
      const current = closest.get(item.station_id)
      if (!current || Math.abs(Date.parse(item.valid_from) - reference) < Math.abs(Date.parse(current.valid_from) - reference)) closest.set(item.station_id, item)
    }
    return [...closest.values()]
  }, [data, simulationTime])
  useEffect(() => {
    const active = plannedWeather.find((item) => item.station_id === activeStationId)
    if (!active) return onActiveWeather?.(null)
    onActiveWeather?.(active.precipitation_mm_per_hr > 0.05
      ? { kind: 'rain', intensity: Math.min(1, active.precipitation_mm_per_hr / 8) }
      : active.cloud_cover_pct > 35
        ? { kind: 'cloud', intensity: active.cloud_cover_pct / 100 }
        : { kind: 'clear', intensity: 1 })
  }, [activeStationId, plannedWeather, onActiveWeather])
  useEffect(() => {
    const start = simulationTime ? new Date(simulationTime) : new Date()
    const end = new Date(start.getTime() + 60 * 60 * 1000)
    client.request<WeatherSnapshot[]>('/weather', {
      method: 'POST',
      body: JSON.stringify({ start_at: start.toISOString(), end_at: end.toISOString() }),
    }).then((result) => {
      if (!Array.isArray(result)) throw new Error('Weather API returned an invalid response.')
      setData(result)
      setError('')
    }).catch((reason: unknown) => {
      const message = typeof reason === 'object' && reason && 'message' in reason
        ? String(reason.message) : 'Live Open-Meteo data is unavailable.'
      setError(message)
    })
  }, [client, weatherHour])
  return <section className="live-weather">
    <span className="eyebrow">LIVE OPEN-METEO FORECAST · ACTIVE PLAN ONLY</span><h2>Planned-station weather</h2>
    {error && <div className="weather-error"><b>Weather unavailable</b><span>{error}</span><small>Complete scenario setup and verify the two AGCC weather settings.</small></div>}
    {!error && data.length === 0 && <p>The active plan has no stations requiring weather data.</p>}
    <div className="weather-grid">{plannedWeather.map((item) => <article key={item.station_id}>
      <span>{item.station_id.replace('station_demo_', '').replaceAll('_', ' ')}<AssumptionMark reason="The hybrid catalogue station identity and coordinates are unverified."/></span>
      <strong>{item.temperature_c.toFixed(1)}°C</strong><dl>
        <div><dt>Precipitation</dt><dd>{item.precipitation_mm_per_hr.toFixed(1)} mm/h</dd></div>
        <div><dt>Humidity</dt><dd>{item.relative_humidity_pct.toFixed(0)}%</dd></div>
        <div><dt>Cloud</dt><dd>{item.cloud_cover_pct.toFixed(0)}%</dd></div>
        <div><dt>Wind</dt><dd>{item.wind_speed_mps.toFixed(1)} m/s</dd></div>
      </dl><small>{item.provenance.source_name} · {item.source_quality} · valid {new Date(item.valid_from).toLocaleTimeString()}</small>
    </article>)}</div>
    <p className="weather-caveat">Open-Meteo rain is a forecast-derived hourly liquid-rain mean<AssumptionMark reason="Hourly rain and showers are preceding-hour accumulations, not instantaneous measurements."/>. RF attenuation remains a modeled estimate.</p>
  </section>
}
