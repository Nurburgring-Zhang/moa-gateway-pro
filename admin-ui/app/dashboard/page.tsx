'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { StatsCard } from '@/components/stats-card';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { DashboardStats } from '@/types';

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
  }, []);

  async function loadStats() {
    try {
      const data = await api.getStats() as unknown as DashboardStats;
      setStats(data);
    } catch {
      // Use mock data for demo
      setStats({
        total_requests: 1284567,
        active_models: 12,
        tokens_today: 8945200,
        avg_latency_ms: 234,
        requests_trend: [120, 150, 180, 220, 190, 250, 280, 310, 290, 340, 380, 420],
        model_health: [
          { name: 'gpt-4o', status: 'healthy', requests: 45230 },
          { name: 'claude-3.5-sonnet', status: 'healthy', requests: 38100 },
          { name: 'gemini-pro', status: 'degraded', requests: 12500 },
          { name: 'qwen-max', status: 'healthy', requests: 28900 },
          { name: 'deepseek-v2', status: 'healthy', requests: 19800 },
        ],
      });
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!stats) return null;

  const maxTrend = Math.max(...stats.requests_trend);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">仪表板</h1>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatsCard
          title="总请求量"
          value={stats.total_requests}
          icon="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
          color="blue"
          trend={12}
        />
        <StatsCard
          title="活跃模型"
          value={stats.active_models}
          icon="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
          color="green"
        />
        <StatsCard
          title="今日Token消耗"
          value={stats.tokens_today}
          icon="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
          color="purple"
          trend={-3}
        />
        <StatsCard
          title="平均延迟"
          value={`${stats.avg_latency_ms}ms`}
          icon="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
          color="orange"
          trend={-8}
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Request Trend Chart */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>请求量趋势 (最近12小时)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-end gap-2 h-40">
              {stats.requests_trend.map((val, i) => (
                <div key={i} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full bg-blue-500 rounded-t transition-all hover:bg-blue-600"
                    style={{ height: `${(val / maxTrend) * 100}%` }}
                  />
                  <span className="text-xs text-gray-400">{i + 1}h</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Model Health */}
        <Card>
          <CardHeader>
            <CardTitle>模型健康状态</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {stats.model_health.map((model) => (
                <div key={model.name} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`h-2 w-2 rounded-full ${
                      model.status === 'healthy' ? 'bg-green-500' :
                      model.status === 'degraded' ? 'bg-yellow-500' : 'bg-red-500'
                    }`} />
                    <span className="text-sm font-medium text-gray-700">{model.name}</span>
                  </div>
                  <Badge variant={
                    model.status === 'healthy' ? 'success' :
                    model.status === 'degraded' ? 'warning' : 'error'
                  }>
                    {model.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
