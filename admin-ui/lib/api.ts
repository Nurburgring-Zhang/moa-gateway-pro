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
}

export const api = new ApiClient();
