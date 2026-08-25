export type OrbitDraft = { altitude_km: number; inclination_deg: number; raan_deg: number; phase_deg: number; epoch: string }
export type OrbitRing = { radius: number; inclination_rad: number; raan_rad: number; phase_rad: number }
const DEG = Math.PI / 180
export const orbitToRing = (orbit: OrbitDraft): OrbitRing => ({
  radius: (6378.137 + orbit.altitude_km) / 6378.137,
  inclination_rad: orbit.inclination_deg * DEG,
  raan_rad: orbit.raan_deg * DEG,
  phase_rad: orbit.phase_deg * DEG,
})
export const ringToOrbit = (ring: OrbitRing, epoch: string): OrbitDraft => ({
  altitude_km: (ring.radius * 6378.137) - 6378.137,
  inclination_deg: ring.inclination_rad / DEG,
  raan_deg: ((ring.raan_rad / DEG) % 360 + 360) % 360,
  phase_deg: ((ring.phase_rad / DEG) % 360 + 360) % 360,
  epoch,
})
