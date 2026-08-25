import { create } from 'zustand'
import { DRAFT_KEY } from './api'
import type { OrbitDraft } from './orbit'

export type MissionMode = 'prediction' | 'live' | 'branch'
type LegacyModeState = { delivered: number; predictedFinal: number; shortfall: number; running: boolean }
export type Draft = { orbit: OrbitDraft; band: string; frequency: number; rate: number; protocolEfficiency: number; polarization: 'horizontal' | 'vertical' | 'circular'; stations: string[]; required: number; deadline: string; budget: number; preference: string }
const now = new Date()
const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000)
const initialDraft: Draft = { orbit: { altitude_km: 550, inclination_deg: 53, raan_deg: 20, phase_deg: 10, epoch: now.toISOString() }, band: 'X', frequency: 9.6, rate: 100, protocolEfficiency: .9, polarization: 'circular', stations: ['station_demo_southafrica'], required: 3000, deadline: tomorrow.toISOString(), budget: 500, preference: 'fastest' }
const restored = (): Draft => { try { const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || '') as Partial<Draft>; return { ...initialDraft, ...saved, orbit: { ...initialDraft.orbit, ...saved.orbit } } } catch { return initialDraft } }
const save = (draft: Draft) => sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft))

type Store = { draft: Draft; appliedDraft: Draft | null; revision: number; mode: MissionMode; prediction: LegacyModeState; branch: LegacyModeState; setMode: (m: MissionMode) => void; updateDraft: (p: Partial<Draft>) => void; updateOrbit: (p: Partial<OrbitDraft>) => void; applyDraft: () => void }
export const useMissionStore = create<Store>((set) => ({
  draft: restored(), appliedDraft: null, revision: 0, mode: 'prediction',
  prediction: { delivered: 0, predictedFinal: 0, shortfall: 0, running: false },
  branch: { delivered: 0, predictedFinal: 0, shortfall: 0, running: false },
  setMode: (mode) => set({ mode }),
  updateDraft: (patch) => set((state) => { const draft = { ...state.draft, ...patch }; save(draft); return { draft } }),
  updateOrbit: (patch) => set((state) => { const draft = { ...state.draft, orbit: { ...state.draft.orbit, ...patch } }; save(draft); return { draft } }),
  applyDraft: () => set((state) => ({ appliedDraft: structuredClone(state.draft), revision: state.revision + 1 })),
}))
