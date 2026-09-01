/**
 * Gateway HTTP + SSE client.
 *
 * Talks to the FastAPI MOA gateway over plain fetch. Every authenticated call
 * sends `Authorization: Bearer <apiKey>` exactly as moa_gateway/auth.py
 * expects (it accepts both `Bearer <key>` and a raw key; we always use the
 * Bearer form).
 *
 * SSE note: the browser EventSource API cannot set custom headers, and the
 * dialogue stream requires the Authorization header. We therefore consume the
 * `text/event-stream` response body manually with fetch + ReadableStream,
 * parsing `data: {...}` frames and ignoring `: keep-alive` comments. This is
 * the same wire format FastAPI's StreamingResponse emits (see
 * moa_gateway/routes/dialogue.py :: _sse_line).
 */

import type {
  CreateRoomRequest,
  DialogueEvent,
  GatewayConfig,
  HealthSummary,
  ModelListResponse,
  PostMessageResponse,
  ProbeResult,
  ReadinessSummary,
  RoomDetailResponse,
  RoomListResponse
} from './types'

/** Error carrying an HTTP status (0 = network failure) + gateway detail. */
export class GatewayError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'GatewayError'
    this.status = status
  }
}

const DEFAULT_TIMEOUT_MS = 30_000
/** A full multi-AI turn can take minutes; give the POST that long. */
const TURN_TIMEOUT_MS = 300_000

/** Normalize a user-typed gateway URL: scheme default http, strip trailing '/'. */
export function normalizeBaseUrl(input: string): string {
  let url = input.trim()
  if (!url) return ''
  if (!/^https?:\/\//i.test(url)) {
    url = `http://${url}`
  }
  // Validate by letting the URL parser reject garbage.
  const parsed = new URL(url)
  parsed.hash = ''
  parsed.search = ''
  let out = parsed.toString()
  while (out.endsWith('/')) out = out.slice(0, -1)
  return out
}

async function parseErrorDetail(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (body.detail !== undefined) return JSON.stringify(body.detail)
  } catch {
    // Non-JSON error body — fall through to the status text.
  }
  return resp.statusText || `HTTP ${resp.status}`
}

export class GatewayClient {
  private readonly baseUrl: string
  private readonly apiKey: string

  constructor(config: GatewayConfig) {
    this.baseUrl = config.baseUrl
    this.apiKey = config.apiKey
  }

  get url(): string {
    return this.baseUrl
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { Accept: 'application/json', ...extra }
    if (this.apiKey) h['Authorization'] = `Bearer ${this.apiKey}`
    return h
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs: number = DEFAULT_TIMEOUT_MS,
    externalSignal?: AbortSignal
  ): Promise<T> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const onExternalAbort = (): void => controller.abort()
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort()
      else externalSignal.addEventListener('abort', onExternalAbort, { once: true })
    }
    let resp: Response
    try {
      resp = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: this.headers(init.headers as Record<string, string> | undefined),
        signal: controller.signal
      })
    } catch (err) {
      const aborted = controller.signal.aborted
      const externalAbort = externalSignal?.aborted === true
      throw new GatewayError(
        0,
        externalAbort
          ? '请求已取消'
          : aborted
            ? `请求超时（${Math.round(timeoutMs / 1000)}s）`
            : `无法连接网关：${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      clearTimeout(timer)
      if (externalSignal) externalSignal.removeEventListener('abort', onExternalAbort)
    }

    if (!resp.ok) {
      throw new GatewayError(resp.status, await parseErrorDetail(resp))
    }
    return (await resp.json()) as T
  }

  // ==================== Health ====================

  /** GET /health — no auth required. */
  health(): Promise<HealthSummary> {
    return this.request<HealthSummary>('/health')
  }

  /** GET /health/ready — no auth required; 503 body still parsed. */
  async readiness(): Promise<ReadinessSummary> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)
    let resp: Response
    try {
      resp = await fetch(`${this.baseUrl}/health/ready`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal
      })
    } catch (err) {
      throw new GatewayError(
        0,
        controller.signal.aborted
          ? '探测超时'
          : `无法连接网关：${err instanceof Error ? err.message : String(err)}`
      )
    } finally {
      clearTimeout(timer)
    }
    // 503 is a legitimate "not ready" answer — parse the body either way.
    try {
      return (await resp.json()) as ReadinessSummary
    } catch {
      throw new GatewayError(resp.status, `网关返回了不可解析的就绪响应 (HTTP ${resp.status})`)
    }
  }

  // ==================== Models ====================

  /** GET /v1/models — includes gateway presets and real enabled endpoints. */
  listModels(): Promise<ModelListResponse> {
    return this.request<ModelListResponse>('/v1/models')
  }

  // ==================== Dialogue rooms ====================

  listRooms(): Promise<RoomListResponse> {
    return this.request<RoomListResponse>('/v1/dialogue/rooms?limit=200')
  }

  getRoom(roomId: string): Promise<RoomDetailResponse> {
    return this.request<RoomDetailResponse>(
      `/v1/dialogue/rooms/${encodeURIComponent(roomId)}?limit=500`
    )
  }

  createRoom(req: CreateRoomRequest): Promise<RoomDetailResponse['room']> {
    return this.request<RoomDetailResponse['room']>('/v1/dialogue/rooms', {
      method: 'POST',
      body: JSON.stringify(req)
    })
  }

  deleteRoom(roomId: string): Promise<{ status: string; room_id: string }> {
    return this.request(`/v1/dialogue/rooms/${encodeURIComponent(roomId)}`, {
      method: 'DELETE'
    })
  }

  /**
   * POST a user message and trigger one multi-AI round.
   * `stream=true` asks the engine to emit real token deltas on the SSE stream.
   * The POST itself blocks until the whole round finishes, hence the long
   * timeout. The optional external signal lets the UI abandon waiting (e.g.
   * the user left the room); the gateway keeps processing the turn server-side.
   */
  postMessage(
    roomId: string,
    content: string,
    externalSignal?: AbortSignal
  ): Promise<PostMessageResponse> {
    return this.request<PostMessageResponse>(
      `/v1/dialogue/rooms/${encodeURIComponent(roomId)}/messages?stream=true`,
      { method: 'POST', body: JSON.stringify({ content }) },
      TURN_TIMEOUT_MS,
      externalSignal
    )
  }

  // ==================== SSE ====================

  /**
   * Subscribe to a room's SSE stream. Returns a close() function.
   *
   * replay=false — we already load persisted history via getRoom(); replaying
   * the engine's in-memory round buffer would duplicate messages.
   * live=true    — keep the connection open for subsequent turns.
   */
  streamRoom(
    roomId: string,
    onEvent: (ev: DialogueEvent) => void,
    onError: (err: GatewayError) => void
  ): () => void {
    const controller = new AbortController()
    let closed = false

    const consume = async (): Promise<void> => {
      let resp: Response
      try {
        resp = await fetch(
          `${this.baseUrl}/v1/dialogue/rooms/${encodeURIComponent(roomId)}/stream?replay=false&live=true`,
          { headers: this.headers(), signal: controller.signal }
        )
      } catch (err) {
        if (!closed) {
          onError(
            new GatewayError(
              0,
              controller.signal.aborted
                ? '流连接已取消'
                : `实时流连接失败：${err instanceof Error ? err.message : String(err)}`
            )
          )
        }
        return
      }
      if (!resp.ok || !resp.body) {
        if (!closed) onError(new GatewayError(resp.status, await parseErrorDetail(resp)))
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          // SSE frames are separated by a blank line.
          let sep: number
          while ((sep = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sep)
            buffer = buffer.slice(sep + 2)
            const ev = parseSseFrame(frame)
            if (ev) onEvent(ev)
          }
        }
        if (!closed) {
          onError(new GatewayError(0, '实时流已被服务端关闭'))
        }
      } catch (err) {
        if (!closed) {
          onError(
            new GatewayError(
              0,
              controller.signal.aborted
                ? '流连接已取消'
                : `实时流读取失败：${err instanceof Error ? err.message : String(err)}`
            )
          )
        }
      }
    }

    void consume()

    return () => {
      closed = true
      controller.abort()
    }
  }
}

/** Parse one SSE frame; returns null for keep-alive comments / empty frames. */
function parseSseFrame(frame: string): DialogueEvent | null {
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue // comment / keep-alive
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (dataLines.length === 0) return null
  try {
    return JSON.parse(dataLines.join('\n')) as DialogueEvent
  } catch {
    return null
  }
}

/**
 * Probe a gateway base URL without requiring an API key:
 * hits /health/ready (and /health for version info) and reports the outcome.
 */
export async function probeGateway(rawUrl: string): Promise<ProbeResult> {
  const started = performance.now()
  let baseUrl: string
  try {
    baseUrl = normalizeBaseUrl(rawUrl)
  } catch {
    return {
      ok: false,
      httpStatus: 0,
      latencyMs: 0,
      message: 'URL 格式无效，请输入类似 http://192.168.1.10:8910 的地址'
    }
  }
  if (!baseUrl) {
    return { ok: false, httpStatus: 0, latencyMs: 0, message: '请输入网关地址' }
  }

  const client = new GatewayClient({ baseUrl, apiKey: '' })
  try {
    const readiness = await client.readiness()
    const latencyMs = Math.round(performance.now() - started)
    const ready = readiness.status === 'healthy' || readiness.status === 'degraded'
    let health: HealthSummary | undefined
    try {
      health = await client.health()
    } catch {
      // /health is best-effort metadata; readiness already answered.
    }
    if (!ready) {
      return {
        ok: false,
        // /health/ready answers 503 for both "not_ready" and "unhealthy".
        httpStatus: 503,
        latencyMs,
        readiness,
        health,
        message: `网关可达但尚未就绪（status=${readiness.status}），请稍后重试`
      }
    }
    return {
      ok: true,
      httpStatus: 200,
      latencyMs,
      readiness,
      health,
      message: health
        ? `连接成功 · 网关 v${health.version} · ${health.endpoints_healthy}/${health.endpoints_enabled} 个端点健康`
        : '连接成功'
    }
  } catch (err) {
    const latencyMs = Math.round(performance.now() - started)
    const ge = err instanceof GatewayError ? err : new GatewayError(0, String(err))
    // Distinguish "HTTP answered but not ready" from "nothing listening".
    if (ge.status >= 500) {
      return {
        ok: false,
        httpStatus: ge.status,
        latencyMs,
        message: `网关可达但未就绪：${ge.message}`
      }
    }
    if (ge.status === 404) {
      return {
        ok: false,
        httpStatus: ge.status,
        latencyMs,
        message: '该地址没有 /health/ready 端点，可能不是 MOA Gateway 或版本过旧'
      }
    }
    return { ok: false, httpStatus: ge.status, latencyMs, message: ge.message }
  }
}
