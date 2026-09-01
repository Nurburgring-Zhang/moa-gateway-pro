/**
 * Shared domain types for the MOA Gateway mobile console.
 *
 * These mirror the FastAPI response contracts declared by the gateway:
 *   - moa_gateway/routes/health.py      (/health, /health/ready)
 *   - moa_gateway/routes/models.py      (/v1/models)
 *   - moa_gateway/routes/dialogue.py    (/v1/dialogue/rooms*)
 *   - moa_gateway/dialogue/models.py    (DialogueRoom / DialogueMessage / DialogueEvent)
 *
 * They are intentionally structural (field names + optionality) so the client
 * stays decoupled from the Python models while still being type-safe.
 */

/** Legacy /health payload (moa_gateway/routes/health.py :: health). */
export interface HealthSummary {
  status: string
  version: string
  endpoints_total: number
  endpoints_enabled: number
  endpoints_healthy: number
  mock_endpoints_count: number
  real_endpoints_count: number
  mock_mode: string
}

/** A single component reported by /health/ready. */
export interface ReadinessComponent {
  status: string
  latency_ms: number
  message: string
}

/** /health/ready payload (moa_gateway/ha/health.py :: readiness). */
export interface ReadinessSummary {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'not_ready' | string
  components?: Record<string, ReadinessComponent>
}

/** One entry of /v1/models `data` (moa_gateway/routes/models.py). */
export interface ModelInfo {
  id: string
  object: string
  created: number
  owned_by: string
  description?: string
  permission?: unknown[]
}

export interface ModelListResponse {
  object: string
  data: ModelInfo[]
}

/** Dialogue orchestration modes (moa_gateway/dialogue/models.py :: DialogueMode). */
export type DialogueMode = 'round_robin' | 'parallel_think' | 'free_talk'

export interface Participant {
  endpoint_id: string
  name: string
  persona: string
}

export interface DialogueRoom {
  room_id: string
  topic: string
  mode: DialogueMode
  status: string
  participants: Participant[]
  max_rounds: number
  participant_timeout: number
  current_round: number
  created_at: number
  updated_at: number
}

export interface RoomListResponse {
  rooms: DialogueRoom[]
  total: number
  limit: number
  offset: number
}

/** A single persisted dialogue message (dialogue/models.py :: DialogueMessage). */
export interface DialogueMessage {
  message_id: string
  room_id: string
  round: number
  speaker: string
  role: 'user' | 'assistant' | 'system'
  content: string
  endpoint_id?: string | null
  status: 'ok' | 'error' | 'timeout' | string
  mock: boolean
  error?: string | null
  latency_ms: number
  prompt_tokens: number
  completion_tokens: number
  created_at: number
}

export interface RoomDetailResponse {
  room: DialogueRoom
  messages: DialogueMessage[]
  total_messages: number
  limit: number
  offset: number
}

/** SSE event pushed on /v1/dialogue/rooms/{id}/stream (DialogueEvent). */
export interface DialogueEvent {
  type:
    | 'turn_start'
    | 'round_start'
    | 'message_start'
    | 'delta'
    | 'message_end'
    | 'moderator'
    | 'round_end'
    | 'turn_complete'
    | string
  room_id: string
  round: number
  speaker?: string
  delta?: string | null
  final?: string | null
  status?: string
  mock?: boolean
  error?: string | null
  step?: number | null
  ts?: number
}

export interface PostMessageResponse {
  room_id: string
  round: number
  mode: DialogueMode
  responses: DialogueMessage[]
  ok_count: number
  mock_used: boolean
}

/** Body for POST /v1/dialogue/rooms. */
export interface CreateRoomRequest {
  topic: string
  mode: DialogueMode
  participants: Array<{ endpoint_id: string; name: string; persona: string }>
  max_rounds: number
  participant_timeout: number
}

/** Connection settings the user configures on the setup screen. */
export interface GatewayConfig {
  /** Normalized base URL, e.g. `http://192.168.1.10:8910` (no trailing slash). */
  baseUrl: string
  /** Gateway API key sent as `Authorization: Bearer <key>`. */
  apiKey: string
}

/** Result of probing a gateway base URL. */
export interface ProbeResult {
  ok: boolean
  /** HTTP status of /health/ready (0 when the network itself failed). */
  httpStatus: number
  /** Milliseconds the probe took. */
  latencyMs: number
  readiness?: ReadinessSummary
  health?: HealthSummary
  /** Human-readable failure reason when ok === false. */
  message: string
}
