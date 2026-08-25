import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { useMissionStore } from './store'
import { mockApiFetch } from './testFixtures'

vi.mock('./GlobeView', () => ({ GlobeView: () => <div>Earth</div> }))

beforeEach(() => { const now = new Date(); sessionStorage.clear(); history.replaceState({}, '', '/'); mockApiFetch.mockClear(); vi.stubGlobal('fetch', mockApiFetch); useMissionStore.setState({ mode: 'prediction', appliedDraft: null, revision: 0, draft: { orbit: { altitude_km: 550, inclination_deg: 53, raan_deg: 20, phase_deg: 10, epoch: now.toISOString() }, band: 'X', frequency: 9.6, rate: 100, protocolEfficiency: .9, polarization: 'circular', stations: ['station_demo_southafrica'], required: 3000, deadline: new Date(now.getTime() + 86400000).toISOString(), budget: 500, preference: 'fastest' } }) })

test('keeps prediction and anomaly timelines isolated', async () => {
  const user = userEvent.setup(); render(<App />)
  await user.click(screen.getByRole('button', { name: '4 Mission' })); await user.click(screen.getByRole('button', { name: 'Apply & create isolated timelines' }))
  await screen.findByText('Downlink completion')
  await user.click(screen.getByRole('tab', { name: 'Anomalies' }))
  await waitFor(() => expect(screen.getByText('Describe what changed')).toBeTruthy())
  expect(screen.getByPlaceholderText(/Example:/)).toBeTruthy()
  expect(sessionStorage.getItem('agcc.session.prediction.v2')).toBeTruthy()
  expect(sessionStorage.getItem('agcc.session.anomaly.v2')).toBeTruthy()
})
