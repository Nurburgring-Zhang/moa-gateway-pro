export interface Model {
  id: string;
  name: string;
  provider: string;
  status: 'active' | 'inactive' | 'error';
  weight: number;
  capabilities: string[];
  created_at: string;
}

export interface Endpoint {
  id: string;
  path: string;
  method: string;
  enabled: boolean;
  health_status: 'healthy' | 'degraded' | 'unhealthy';
  latency_ms: number;
  last_checked: string;
}

export interface Capability {
  name: string;
  display_name: string;
  enabled: boolean;
  provider: string;
  description: string;
  config: Record<string, unknown>;
}

export interface ApiKey {
  id: string;
  name: string;
  key: string;
  quota: number;
  used: number;
  created_at: string;
  last_used: string | null;
  status: 'active' | 'revoked';
}

export interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'warn' | 'error' | 'debug';
  message: string;
  source: string;
  metadata?: Record<string, unknown>;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  status: 'active' | 'paused' | 'error';
  last_run: string;
  next_run: string;
  steps: number;
}

export interface User {
  id: string;
  username: string;
  email: string;
  role: 'admin' | 'operator' | 'user' | 'readonly';
  status: 'active' | 'disabled';
  last_login: string;
  created_at: string;
}

export interface DashboardStats {
  total_requests: number;
  active_models: number;
  tokens_today: number;
  avg_latency_ms: number;
  requests_trend: number[];
  model_health: Array<{
    name: string;
    status: string;
    requests: number;
  }>;
}

export interface SystemSettings {
  cache: {
    enabled: boolean;
    ttl_seconds: number;
    max_size_mb: number;
    backend: string;
  };
  database: {
    url: string;
    pool_size: number;
    max_overflow: number;
  };
  mcp: {
    enabled: boolean;
    server_url: string;
    timeout_seconds: number;
  };
  rate_limit: {
    enabled: boolean;
    requests_per_minute: number;
    burst_size: number;
  };
}

// =====================================================================
// v4.1 integration types (M1-M11 backend modules)
// =====================================================================

// ---- M1 routing strategies ----
export interface RoutingStrategyInfo {
  name: string;
  description: string;
  mode: string;
  internal: boolean;
}

export interface RoutingStrategiesResponse {
  enabled: boolean;
  default_strategy: string;
  count: number;
  strategies: RoutingStrategyInfo[];
}

export interface RoutingSelection {
  endpoint_id: string;
  rank: number;
  score: number | null;
  reason: string;
}

export interface RoutingResolveResponse {
  strategy: string;
  mode: string;
  selected: string | null;
  ordered: string[];
  scores: Record<string, number>;
  selections: RoutingSelection[];
  dry_run: boolean;
  candidate_count: number;
}

export interface RoutingTelemetryEndpoint {
  endpoint_id: string;
  request_count: number;
  window_count: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  stddev_latency_ms: number;
  success_rate: number;
  error_rate: number;
}

export interface RoutingTelemetryResponse {
  history_window: number;
  endpoint_count: number;
  endpoints: RoutingTelemetryEndpoint[];
}

// ---- M2 quota scheduler ----
export interface QuotaValueInfo {
  dimension?: string;
  limit?: number | null;
  used?: number | null;
  remaining?: number | null;
  reset_at?: string | null;
  unit?: string | null;
  source?: string;
  confidence?: string;
}

export interface QuotaEndpointSummary {
  provider_id: string;
  connection_id: string;
  endpoint_id: string;
  supported: boolean;
  fetched_at: string | null;
  status: string;
  error: string | null;
  values: QuotaValueInfo[];
}

export interface QuotaStatusResponse {
  enabled: boolean;
  fail_open: boolean;
  poll_interval_s: number;
  fast_poll_interval_s: number;
  warn_threshold: number;
  exhaust_threshold: number;
  endpoint_count: number;
  endpoints: QuotaEndpointSummary[];
}

export interface QuotaSnapshotRow {
  id: number;
  endpoint_id: string;
  provider_id: string;
  connection_id: string;
  captured_at: string | null;
  status: string;
  values?: QuotaValueInfo[];
  change_key?: string;
}

export interface QuotaCheckResponse {
  allowed: boolean;
  reason: string;
  status: string;
  retry_after_ms: number | null;
}

// ---- M3 prompt compression (response shapes owned by the backend module;
// kept intentionally loose so the UI renders whatever the gateway returns) ----
export interface CompressionCompressResponse {
  [key: string]: unknown;
}

// ---- M4 free-tier catalog ----
export interface FreeTierEntry {
  key?: string;
  provider?: string;
  modelId?: string;
  displayName?: string;
  monthlyTokens?: number;
  creditTokens?: number;
  freeType?: string;
  poolKey?: string;
  tos?: string;
  [key: string]: unknown;
}

// ---- M10 memory ----
export interface MemoryItem {
  id: number;
  effective_user_id: string;
  base_user_id: string;
  repository_slug: string;
  memory_type: string;
  content: string;
  content_hash?: string;
  role: string;
  source: string;
  session_id: string | null;
  group_id: string | null;
  chunk_index: number;
  chunk_count: number;
  created_at: number;
  updated_at: number;
}

export interface MemoryRecallResponse {
  retrieved: boolean;
  item_count: number;
  latency_ms?: number;
  backend?: string;
  context?: string;
  skip_reason?: string;
  items?: Array<Record<string, unknown>>;
}

// ---- M7 skillhub ----
export interface SkillInfo {
  name: string;
  name_zh?: string;
  description: string;
  description_zh?: string;
  triggers: string[];
  context?: string;
  agent?: string;
  argument_hint?: string;
  allowed_tools: string[];
  forbidden_tools: string[];
  model?: string;
  user_invocable: boolean;
  disable_model_invocation: boolean;
  auto_summarize: boolean;
  always_show: boolean;
  fork_agent: boolean;
  hooks: Record<string, unknown>;
  source: 'bundled' | 'extra' | 'user';
  priority: number;
  dir_path: string;
  content_chars: number;
  content?: string;
}

export interface SkillListResponse {
  skills: SkillInfo[];
  count: number;
  sources: Record<string, number>;
}

export interface SkillSearchResultItem {
  skill: SkillInfo;
  score: number;
  breakdown: Record<string, number>;
}

export interface SkillSearchResponse {
  query: string;
  results: SkillSearchResultItem[];
  count: number;
}

export interface SkillDetailResponse {
  skill: SkillInfo;
  usage?: Record<string, unknown>;
}

export interface SkillInvokeResponse {
  skill?: string;
  task?: string;
  content?: string;
  endpoint_id?: string;
  model?: string;
  provider?: string;
  finish_reason?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  latency_ms?: number;
  evolution?: Record<string, unknown>;
  [key: string]: unknown;
}

// ---- M8 IM channels ----
export interface ChannelStatus {
  platform: string;
  state: string;
  configured: boolean;
  running: boolean;
  last_activity: number | string | null;
  required_env: string[];
  missing_env: string[];
}

export interface ChannelListResponse {
  channels: ChannelStatus[];
  count: number;
  configured: number;
  enabled: string[];
}
