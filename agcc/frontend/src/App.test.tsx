import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { DRAFT_KEY } from './api'
import { orbitToRing, ringToOrbit } from './orbit'
import { useMissionStore } from './store'
import { mockApiFetch } from './testFixtures'

vi.mock('./GlobeView', () => ({ GlobeView: () => <div aria-label="globe">Earth</div> }))

beforeEach(() => {
  sessionStorage.clear(); history.replaceState({}, '', '/'); mockApiFetch.mockClear(); vi.stubGlobal('fetch', mockApiFetch)
  const now = new Date(); const deadline = new Date(now.getTime() + 86400000)
  useMissionStore.setState({ mode: 'prediction', appliedDraft: null, revision: 0, draft: { orbit: { altitude_km: 550, inclination_deg: 53, raan_deg: 20, phase_deg: 10, epoch: now.toISOString() }, band: 'X', frequency: 9.6, rate: 100, protocolEfficiency: .9, polarization: 'circular', stations: ['station_demo_southafrica'], required: 3000, deadline: deadline.toISOString(), budget: 500, preference: 'fastest' } })
})

test('lands in the custom satellite builder and initializes epoch', async () => {
  render(<App />)
  expect(screen.getByRole('heading', { name: 'Orbit' })).toBeTruthy()
  expect(String((screen.getByLabelText('Epoch UTC (initialized from this device)') as HTMLInputElement).value)).toMatch(/^\d{4}-/)
  expect(await screen.findByText('BACKEND READY')).toBeTruthy()
})

test('validates incompatible band and frequency combinations', async () => {
  const user = userEvent.setup(); render(<App />)
  await user.click(screen.getByRole('button', { name: '2 Communications' }))
  await user.selectOptions(screen.getByLabelText('Band'), 'S')
  const frequency = screen.getByRole('spinbutton', { name: /Exact carrier frequency/ })
  await user.clear(frequency); await user.type(frequency, '9.6')
  expect(screen.getByText(/S-band requires 2–4 GHz/)).toBeTruthy()
})

test('keeps synchronized orbit state and session draft', async () => {
  const user = userEvent.setup(); render(<App />)
  const inclination = screen.getByLabelText('Inclination (degrees)')
  await user.clear(inclination); await user.type(inclination, '97.6')
  expect(useMissionStore.getState().draft.orbit.inclination_deg).toBe(97.6)
  expect(sessionStorage.getItem(DRAFT_KEY)).toContain('97.6')
})

test('applies once and creates the prediction timeline', async () => {
  const user = userEvent.setup(); render(<App />)
  await user.click(screen.getByRole('button', { name: '4 Mission' }))
  await user.click(screen.getByRole('button', { name: 'Apply & create isolated timelines' }))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/v1/scenario', expect.objectContaining({ method: 'POST' })))
  expect(await screen.findByText('Downlink completion')).toBeTruthy()
  expect(screen.getByText('INTERNAL SIMULATION TIME')).toBeTruthy()
  expect(screen.getByText('FEASIBLE · 3000.00 MB fully scheduled')).toBeTruthy()
  expect(screen.getByText(/Frozen forecast ledger/)).toBeTruthy()
})

test('orbit ring conversion round trips', () => {
  const orbit = useMissionStore.getState().draft.orbit
  const restored = ringToOrbit(orbitToRing(orbit), orbit.epoch)
  expect(restored.inclination_deg).toBeCloseTo(orbit.inclination_deg, 8)
  expect(restored.raan_deg).toBeCloseTo(orbit.raan_deg, 8)
})
