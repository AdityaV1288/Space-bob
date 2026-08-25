export const SESSION_KEY = 'agcc.session.v1'
export const DRAFT_KEY = 'agcc.draft.v1'

export type ApiError = { code: string; message: string; entity_refs: Record<string, string>; details: Record<string, unknown> }

const pendingSessions = new Map<string, Promise<string>>()

export class AgccClient {
  constructor(private readonly baseUrl = '', readonly sessionKey = SESSION_KEY) {}

  async createSession(): Promise<string> {
    const response = await fetch(`${this.baseUrl}/api/v1/sessions`, { method: 'POST' })
    if (!response.ok) throw await this.error(response)
    return (await response.json() as { session_id: string }).session_id
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const sessionId = sessionStorage.getItem(this.sessionKey)
    const response = await fetch(`${this.baseUrl}/api/v1${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(sessionId ? { 'X-AGCC-Session': sessionId } : {}), ...init.headers },
    })
    if (!response.ok) throw await this.error(response)
    return response.json() as Promise<T>
  }

  streamUrl(): string { return `${this.baseUrl}/api/v1/events/stream` }

  async streamEvents(
    onEvent: (event: unknown) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const sessionId = sessionStorage.getItem(this.sessionKey)
    const response = await fetch(this.streamUrl(), {
      headers: sessionId ? { 'X-AGCC-Session': sessionId } : {}, signal,
    })
    if (!response.ok || !response.body) throw await this.error(response)
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const data = block.split('\n').find((line) => line.startsWith('data: '))
        if (data) onEvent(JSON.parse(data.slice(6)))
      }
    }
  }

  deleteWithBeacon(): void {
    const sessionId = sessionStorage.getItem(this.sessionKey)
    if (sessionId) navigator.sendBeacon(`${this.baseUrl}/api/v1/sessions/${sessionId}`)
  }

  private async error(response: Response): Promise<ApiError> {
    const payload = await response.json().catch(() => ({})) as { error?: ApiError; detail?: ApiError }
    const error = payload.error ?? payload.detail ?? { code: 'HTTP_ERROR', message: response.statusText, entity_refs: {}, details: {} }
    const violations = Array.isArray(error.details?.violations)
      ? error.details.violations.map(String)
      : []
    if (violations.length) return { ...error, message: `${error.message}: ${violations.join(' · ')}` }
    const validation = Array.isArray(error.details?.errors) ? error.details.errors[0] as { loc?: unknown[]; msg?: string } : null
    if (!validation?.msg) return error
    const location = validation.loc?.join('.')
    return { ...error, message: `${error.message}: ${location ? `${location}: ` : ''}${validation.msg}` }
  }
}

export async function ensureSession(client: AgccClient): Promise<string> {
  const existing = sessionStorage.getItem(client.sessionKey)
  if (existing) return existing
  let pendingSession = pendingSessions.get(client.sessionKey)
  if (!pendingSession) {
    pendingSession = client.createSession().then((created) => {
      sessionStorage.setItem(client.sessionKey, created)
      return created
    }).finally(() => { pendingSessions.delete(client.sessionKey) })
    pendingSessions.set(client.sessionKey, pendingSession)
  }
  return pendingSession
}

export function resetSession(client: AgccClient): void {
  sessionStorage.removeItem(client.sessionKey)
  pendingSessions.delete(client.sessionKey)
}
