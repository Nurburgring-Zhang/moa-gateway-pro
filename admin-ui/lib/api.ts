import type {
  ChannelListResponse,
  FreeTierEntry,
  MemoryItem,
  MemoryRecallResponse,
  QuotaCheckResponse,
  QuotaStatusResponse,
  RoutingResolveResponse,
  RoutingStrategiesResponse,
  RoutingTelemetryResponse,
  SkillDetailResponse,
  SkillInvokeResponse,
  SkillListResponse,
  SkillSearchResponse,
} from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8910';

class ApiClient {
  private token: string = '';

  setToken(token: string) {
    this.token = token;
  }

  getToken(): string {
    return this.token;
  }

  private async request<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
    // Audit fix (P1): React runs child effects before the parent layout's
    // initAuth() effect, so after a hard refresh / direct link the first
    // request could fire while this.token is still empty. Recover the token
    // synchronously from localStorage before sending (browser only).
    if (!this.token && typeof window !== 'undefined') {
      const stored = window.localStorage.getItem('moa_admin_token');
      if (stored) this.token = stored;
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
    };

    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { ...headers, ...(options.headers as Record<string, string>) },
    });

    if (res.status === 401) {
      if (typeof window !== 'undefined') {
        // Audit fix: clear the SAME keys auth.ts stores ('moa_admin_token' /
        // 'moa_admin_user'), otherwise a stale JWT persists after logout.
        localStorage.removeItem('moa_admin_token');
        localStorage.removeItem('moa_admin_user');
        this.token = '';
        window.location.href = '/login';
      }
      throw new Error('Unauthorized');
    }

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`API Error ${res.status}: ${body}`);
    }

    if (res.status === 204) return null as T;
    return res.json();
  }

  // Auth
  login(username: string, password: string) {
    return this.request<{ access_token: string; token_type: string }>(
      '/api/auth/login',
      { method: 'POST', body: JSON.stringify({ username, password }) }
    );
  }

  // Models — admin view (real endpoint shapes), NOT the OpenAI /v1/models list.
  // Audit fix: /v1/models returns {id,object,created,owned_by,...} which has none
  // of the name/provider/status/weight/capabilities fields the admin table renders.
  getModels() {
    return this.request<{ data: Array<Record<string, unknown>> }>('/api/admin/models');
  }

  createModel(data: Record<string, unknown>) {
    return this.request('/api/admin/models', { method: 'POST', body: JSON.stringify(data) });
  }

  updateModel(id: string, data: Record<string, unknown>) {
    return this.request(`/api/admin/models/${id}`, { method: 'PUT', body: JSON.stringify(data) });
  }

  deleteModel(id: string) {
    return this.request(`/api/admin/models/${id}`, { method: 'DELETE' });
  }

  // Endpoints — backend returns a snapshot envelope; unwrap to the endpoint list.
  async getEndpoints(): Promise<Array<Record<string, unknown>>> {
    const snap = await this.request<{ endpoints?: Array<Record<string, unknown>> }>(
      '/api/admin/endpoints'
    );
    return snap.endpoints ?? [];
  }

  updateEndpoint(id: string, data: Record<string, unknown>) {
    return this.request(`/api/admin/endpoints/${id}`, { method: 'PUT', body: JSON.stringify(data) });
  }

  // Capability — backend returns {capabilities: [...]}; unwrap to the list.
  async getCapabilities(): Promise<Array<Record<string, unknown>>> {
    const body = await this.request<{ capabilities?: Array<Record<string, unknown>> }>(
      '/api/admin/capabilities'
    );
    return body.capabilities ?? [];
  }

  updateCapability(name: string, data: Record<string, unknown>) {
    return this.request(`/api/admin/capabilities/${name}`, { method: 'PUT', body: JSON.stringify(data) });
  }

  // Health
  getHealth() {
    return this.request<Record<string, unknown>>('/health');
  }

  // Stats
  getStats() {
    return this.request<Record<string, unknown>>('/api/admin/stats');
  }

  // Logs — backend returns {total, logs: [...]}; unwrap to the list.
  async getLogs(limit = 100, level?: string): Promise<Array<Record<string, unknown>>> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (level) params.set('level', level);
    const body = await this.request<{ logs?: Array<Record<string, unknown>> }>(
      `/v1/observability/logs?${params}`
    );
    return body.logs ?? [];
  }

  // Workflows — backend returns {workflows: [...], total}; unwrap to the list.
  async getWorkflows(): Promise<Array<Record<string, unknown>>> {
    const body = await this.request<{ workflows?: Array<Record<string, unknown>> }>(
      '/v1/workflows'
    );
    return body.workflows ?? [];
  }

  triggerWorkflow(id: string) {
    return this.request(`/v1/workflows/${id}/trigger`, { method: 'POST' });
  }

  // Orchestrator (v3.2.1 backport) — capability registry summary {total, by_type, ...}.
  getOrchestratorCapabilities() {
    return this.request<Record<string, unknown>>('/v1/orchestrator/capabilities');
  }

  runOrchestration(task: string, input: Record<string, unknown>) {
    return this.request<Record<string, unknown>>('/v1/orchestrator/run', {
      method: 'POST',
      body: JSON.stringify({ task, input }),
    });
  }

  // API Keys — backend returns a bare array.
  async getApiKeys(): Promise<Array<Record<string, unknown>>> {
    const body = await this.request<unknown>('/api/admin/api-keys');
    return Array.isArray(body) ? body : [];
  }

  createApiKey(name: string, quota?: number) {
    return this.request('/api/admin/api-keys', {
      method: 'POST',
      body: JSON.stringify({ name, quota_rpm: quota ?? 60 }),
    });
  }

  deleteApiKey(id: string) {
    return this.request(`/api/admin/api-keys/${id}`, { method: 'DELETE' });
  }

  // Users — backend returns {users: [...]}; unwrap to the list.
  async getUsers(): Promise<Array<Record<string, unknown>>> {
    const body = await this.request<{ users?: Array<Record<string, unknown>> }>('/api/admin/users');
    return body.users ?? [];
  }

  createUser(username: string, password: string, role: string) {
    return this.request('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ username, password, role }),
    });
  }

  deleteUser(id: string) {
    return this.request(`/api/admin/users/${id}`, { method: 'DELETE' });
  }

  updateUserRole(id: string, role: string) {
    return this.request(`/api/admin/users/${id}/role`, { method: 'PUT', body: JSON.stringify({ role }) });
  }

  // Settings
  getSettings() {
    return this.request<Record<string, unknown>>('/api/admin/settings');
  }

  updateSettings(data: Record<string, unknown>) {
    return this.request('/api/admin/settings', { method: 'PUT', body: JSON.stringify(data) });
  }

  // ================= v4.1 integration surfaces =================

  // M1 — routing strategies
  getRoutingStrategies() {
    return this.request<RoutingStrategiesResponse>('/v1/routing/strategies');
  }

  resolveRouting(body: {
    candidates: Array<Record<string, unknown>>;
    strategy?: string | null;
    context?: Record<string, unknown> | null;
    dry_run?: boolean;
  }) {
    return this.request<RoutingResolveResponse>('/v1/routing/resolve', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  getRoutingTelemetry() {
    return this.request<RoutingTelemetryResponse>('/v1/routing/telemetry');
  }

  // M2 — quota scheduler
  getQuotaStatus() {
    return this.request<QuotaStatusResponse>('/v1/quota/status');
  }

  getQuotaSnapshots(endpointId?: string, limit = 100) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (endpointId) params.set('endpoint_id', endpointId);
    return this.request<{ count: number; snapshots: Array<Record<string, unknown>> }>(
      `/v1/quota/snapshots?${params}`
    );
  }

  checkQuota(body: { provider_id?: string; connection_id?: string; endpoint_id?: string }) {
    return this.request<QuotaCheckResponse>('/v1/quota/check', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  refreshQuota() {
    return this.request<Record<string, unknown>>('/v1/quota/refresh', {
      method: 'POST',
      body: JSON.stringify({}),
    });
  }

  // M3 — prompt compression (envelopes normalized on the page side)
  getCompressionModes() {
    return this.request<{
      modes: Array<{ name: string; description: string; is_default: boolean }>;
      config?: Record<string, unknown>;
      stacked_default_pipeline?: Array<Record<string, unknown>>;
    }>('/v1/compression/modes');
  }

  getCompressionStats() {
    return this.request<Record<string, unknown>>('/v1/compression/stats');
  }

  compressPrompt(body: { text?: string; messages?: Array<Record<string, unknown>>; mode?: string }) {
    return this.request<Record<string, unknown>>('/v1/compression/compress', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  // M4 — free-tier catalog (catalog.query: page/page_size -> {total, page, page_size, items})
  async getFreeTiers(params: {
    provider?: string;
    regime?: string;
    page: number;
    pageSize: number;
  }): Promise<{ entries: FreeTierEntry[]; total: number | null }> {
    const qs = new URLSearchParams({
      page: String(params.page),
      page_size: String(params.pageSize),
    });
    if (params.provider) qs.set('provider', params.provider);
    if (params.regime) qs.set('regime', params.regime);
    const body = await this.request<Record<string, unknown>>(`/v1/free-tiers?${qs}`);
    // Defensive unwrap: tolerate bare-array or entries-envelope variants too.
    const list = Array.isArray(body)
      ? body
      : Array.isArray(body.items)
        ? body.items
        : Array.isArray(body.entries)
          ? body.entries
          : [];
    const total =
      typeof body.total === 'number'
        ? body.total
        : typeof body.count === 'number'
          ? body.count
          : null;
    return { entries: list as FreeTierEntry[], total };
  }

  getFreeTier(key: string) {
    return this.request<Record<string, unknown>>(`/v1/free-tiers/${encodeURIComponent(key)}`);
  }

  // M10 — memory
  getMemoryItems(params: {
    repository: string;
    memoryType?: string;
    limit: number;
    offset: number;
  }) {
    const qs = new URLSearchParams({
      repository: params.repository,
      limit: String(params.limit),
      offset: String(params.offset),
    });
    if (params.memoryType) qs.set('memory_type', params.memoryType);
    return this.request<{ items: MemoryItem[]; count: number; total: number }>(
      `/v1/memory/items?${qs}`
    );
  }

  deleteMemoryItem(itemId: number, repository: string) {
    const qs = new URLSearchParams({ repository });
    return this.request<{ deleted: boolean; id: number }>(
      `/v1/memory/items/${itemId}?${qs}`,
      { method: 'DELETE' }
    );
  }

  recallMemory(params: { query: string; repository?: string; cwd?: string }) {
    const qs = new URLSearchParams({ query: params.query });
    if (params.repository) qs.set('repository', params.repository);
    if (params.cwd) qs.set('cwd', params.cwd);
    return this.request<MemoryRecallResponse>(`/v1/memory/recall?${qs}`);
  }

  // M7 — skillhub
  getSkills(source?: string) {
    const qs = source ? `?source=${encodeURIComponent(source)}` : '';
    return this.request<SkillListResponse>(`/v1/skills${qs}`);
  }

  searchSkills(query: string, topK = 5) {
    return this.request<SkillSearchResponse>('/v1/skills/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    });
  }

  getSkill(name: string, withContent = false) {
    const qs = withContent ? '?with_content=true' : '';
    return this.request<SkillDetailResponse>(`/v1/skills/${encodeURIComponent(name)}${qs}`);
  }

  createSkill(body: {
    name?: string;
    description?: string;
    content?: string;
    meta?: Record<string, unknown>;
    force_template?: boolean;
  }) {
    return this.request<Record<string, unknown>>('/v1/skills', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  updateSkill(
    name: string,
    body: { content?: string; description?: string; meta?: Record<string, unknown> }
  ) {
    return this.request<Record<string, unknown>>(`/v1/skills/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  deleteSkill(name: string) {
    return this.request<{ deleted: string; dir: string }>(
      `/v1/skills/${encodeURIComponent(name)}`,
      { method: 'DELETE' }
    );
  }

  invokeSkill(name: string, body: { task: string; tier?: string }) {
    return this.request<SkillInvokeResponse>(
      `/v1/skills/${encodeURIComponent(name)}/invoke`,
      { method: 'POST', body: JSON.stringify(body) }
    );
  }

  // M8 — IM channels
  getChannels() {
    return this.request<ChannelListResponse>('/v1/channels');
  }

  sendChannelMessage(name: string, body: { chat_id: string; text: string }) {
    return this.request<Record<string, unknown>>(
      `/v1/channels/${encodeURIComponent(name)}/send`,
      { method: 'POST', body: JSON.stringify(body) }
    );
  }
}

export const api = new ApiClient();
