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
        localStorage.removeItem('token');
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

  // Models
  getModels() {
    return this.request<{ data: Array<Record<string, unknown>> }>('/v1/models');
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

  // Endpoints
  getEndpoints() {
    return this.request<Array<Record<string, unknown>>>('/api/admin/endpoints');
  }

  updateEndpoint(id: string, data: Record<string, unknown>) {
    return this.request(`/api/admin/endpoints/${id}`, { method: 'PUT', body: JSON.stringify(data) });
  }

  // Capability
  getCapabilities() {
    return this.request<Array<Record<string, unknown>>>('/api/admin/capabilities');
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

  // Logs
  getLogs(limit = 100, level?: string) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (level) params.set('level', level);
    return this.request<Array<Record<string, unknown>>>(`/v1/observability/logs?${params}`);
  }

  // Workflows
  getWorkflows() {
    return this.request<Array<Record<string, unknown>>>('/v1/workflows');
  }

  triggerWorkflow(id: string) {
    return this.request(`/v1/workflows/${id}/trigger`, { method: 'POST' });
  }

  // API Keys
  getApiKeys() {
    return this.request<Array<Record<string, unknown>>>('/api/admin/api-keys');
  }

  createApiKey(name: string, quota?: number) {
    return this.request('/api/admin/api-keys', { method: 'POST', body: JSON.stringify({ name, quota }) });
  }

  deleteApiKey(id: string) {
    return this.request(`/api/admin/api-keys/${id}`, { method: 'DELETE' });
  }

  // Users
  getUsers() {
    return this.request<Array<Record<string, unknown>>>('/api/admin/users');
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
