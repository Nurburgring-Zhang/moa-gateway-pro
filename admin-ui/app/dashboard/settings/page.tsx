'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Toggle } from '@/components/ui/toggle';
import type { SystemSettings } from '@/types';

const DEFAULT_SETTINGS: SystemSettings = {
  cache: { enabled: true, ttl_seconds: 300, max_size_mb: 512, backend: 'redis' },
  database: { url: 'postgresql://localhost:5432/moa_gateway', pool_size: 20, max_overflow: 10 },
  mcp: { enabled: true, server_url: 'http://localhost:8920', timeout_seconds: 30 },
  rate_limit: { enabled: true, requests_per_minute: 1000, burst_size: 50 },
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<SystemSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadSettings();
  }, []);

  async function loadSettings() {
    try {
      const data = await api.getSettings();
      if (data) setSettings(data as unknown as SystemSettings);
    } catch {
      // Use defaults
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    try {
      await api.updateSettings(settings as unknown as Record<string, unknown>);
    } catch {
      // Demo
    }
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">系统设置</h1>
        <div className="flex items-center gap-3">
          {saved && <span className="text-sm text-green-600">已保存</span>}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存设置'}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Cache Settings */}
        <Card>
          <CardHeader>
            <CardTitle>缓存配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Toggle
              label="启用缓存"
              checked={settings.cache.enabled}
              onChange={(v) => setSettings({ ...settings, cache: { ...settings.cache, enabled: v } })}
            />
            <Input
              label="TTL (秒)"
              type="number"
              value={String(settings.cache.ttl_seconds)}
              onChange={(e) => setSettings({ ...settings, cache: { ...settings.cache, ttl_seconds: Number(e.target.value) } })}
            />
            <Input
              label="最大缓存 (MB)"
              type="number"
              value={String(settings.cache.max_size_mb)}
              onChange={(e) => setSettings({ ...settings, cache: { ...settings.cache, max_size_mb: Number(e.target.value) } })}
            />
            <Input
              label="后端"
              value={settings.cache.backend}
              onChange={(e) => setSettings({ ...settings, cache: { ...settings.cache, backend: e.target.value } })}
            />
          </CardContent>
        </Card>

        {/* Database Settings */}
        <Card>
          <CardHeader>
            <CardTitle>数据库配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Input
              label="连接地址"
              value={settings.database.url}
              onChange={(e) => setSettings({ ...settings, database: { ...settings.database, url: e.target.value } })}
            />
            <Input
              label="连接池大小"
              type="number"
              value={String(settings.database.pool_size)}
              onChange={(e) => setSettings({ ...settings, database: { ...settings.database, pool_size: Number(e.target.value) } })}
            />
            <Input
              label="最大溢出"
              type="number"
              value={String(settings.database.max_overflow)}
              onChange={(e) => setSettings({ ...settings, database: { ...settings.database, max_overflow: Number(e.target.value) } })}
            />
          </CardContent>
        </Card>

        {/* MCP Settings */}
        <Card>
          <CardHeader>
            <CardTitle>MCP 配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Toggle
              label="启用 MCP"
              checked={settings.mcp.enabled}
              onChange={(v) => setSettings({ ...settings, mcp: { ...settings.mcp, enabled: v } })}
            />
            <Input
              label="服务器地址"
              value={settings.mcp.server_url}
              onChange={(e) => setSettings({ ...settings, mcp: { ...settings.mcp, server_url: e.target.value } })}
            />
            <Input
              label="超时 (秒)"
              type="number"
              value={String(settings.mcp.timeout_seconds)}
              onChange={(e) => setSettings({ ...settings, mcp: { ...settings.mcp, timeout_seconds: Number(e.target.value) } })}
            />
          </CardContent>
        </Card>

        {/* Rate Limit Settings */}
        <Card>
          <CardHeader>
            <CardTitle>速率限制配置</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Toggle
              label="启用速率限制"
              checked={settings.rate_limit.enabled}
              onChange={(v) => setSettings({ ...settings, rate_limit: { ...settings.rate_limit, enabled: v } })}
            />
            <Input
              label="每分钟请求数"
              type="number"
              value={String(settings.rate_limit.requests_per_minute)}
              onChange={(e) => setSettings({ ...settings, rate_limit: { ...settings.rate_limit, requests_per_minute: Number(e.target.value) } })}
            />
            <Input
              label="突发大小"
              type="number"
              value={String(settings.rate_limit.burst_size)}
              onChange={(e) => setSettings({ ...settings, rate_limit: { ...settings.rate_limit, burst_size: Number(e.target.value) } })}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
