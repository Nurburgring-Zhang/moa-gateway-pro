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
  last_used: string;
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
