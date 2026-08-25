export type ContactState = 'complete' | 'active' | 'upcoming'

export const contacts = [
  { id: 'opp_0003', time: '01:14', station: 'East Asia', volume: 176, cost: 42, state: 'complete' as ContactState },
  { id: 'opp_0007', time: '02:52', station: 'South Africa', volume: 765, cost: 91, state: 'active' as ContactState },
  { id: 'opp_0014', time: '04:31', station: 'East Africa', volume: 1440, cost: 173, state: 'upcoming' as ContactState },
  { id: 'opp_0019', time: '06:08', station: 'East Asia', volume: 624, cost: 112, state: 'upcoming' as ContactState },
]

export const opportunities = [
  ...contacts.map((c) => ({ ...c, classification: 'selected', reason: 'Included in approved plan', elevation: 38.7 })),
  { id: 'opp_0002', time: '00:48', station: 'East Africa', volume: 314, cost: 65, classification: 'eligible', reason: 'Feasible, lower objective score', elevation: 24.1 },
  { id: 'opp_0005', time: '02:03', station: 'South America', volume: 522, cost: 116, classification: 'unused', reason: 'Capacity already satisfied', elevation: 31.4 },
  { id: 'opp_0011', time: '03:44', station: 'Arctic', volume: 202, cost: 74, classification: 'rejected', reason: 'Elevation below 10° policy', elevation: 7.8 },
  { id: 'opp_0017', time: '05:26', station: 'Australia', volume: 419, cost: 95, classification: 'candidate', reason: 'Awaiting final feasibility evaluation', elevation: 29.6 },
]

export const setupSteps = ['Identity', 'Custom orbit', 'Communications', 'Stations', 'Data mission', 'Preferences']
export const sourceLabel = 'Task 14 verified golden fixture'
