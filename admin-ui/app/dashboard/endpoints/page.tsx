'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Toggle } from '@/components/ui/toggle';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import type { Endpoint } from '@/types';

export default function EndpointsPage() {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadEndpoints();
  }, []);

  async function loadEndpoints() {
    try {
      const data = await api.getEndpoints();
      setEndpoints(data as unknown as Endpoint[]);
    } catch {
      setEndpoints([
        { id: '1', path: '/v1/chat/completions', method: 'POST', enabled: true, health_status: 'healthy', latency_ms: 180, last_checked: '2024-08-01T10:00:00Z' },
        { id: '2', path: '/v1/completions', method: 'POST', enabled: true, health_status: 'healthy', latency_ms: 150, last_checked: '2024-08-01T10:00:00Z' },
        { id: '3', path: '/v1/embeddings', method: 'POST', enabled: true, health_status: 'healthy', latency_ms: 90, last_checked: '2024-08-01T10:00:00Z' },
        { id: '4', path: '/v1/images/generations', method: 'POST', enabled: true, health_status: 'degraded', latency_ms: 2400, last_checked: '2024-08-01T10:00:00Z' },
        { id: '5', path: '/v1/audio/transcriptions', method: 'POST', enabled: false, health_status: 'unhealthy', latency_ms: 0, last_checked: '2024-08-01T09:00:00Z' },
        { id: '6', path: '/v1/audio/speech', method: 'POST', enabled: true, health_status: 'healthy', latency_ms: 320, last_checked: '2024-08-01T10:00:00Z' },
        { id: '7', path: '/v1/models', method: 'GET', enabled: true, health_status: 'healthy', latency_ms: 12, last_checked: '2024-08-01T10:00:00Z' },
        { id: '8', path: '/v1/workflows', method: 'POST', enabled: true, health_status: 'healthy', latency_ms: 450, last_checked: '2024-08-01T10:00:00Z' },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleToggle(endpoint: Endpoint) {
    const updated = { ...endpoint, enabled: !endpoint.enabled };
    setEndpoints(endpoints.map((e) => (e.id === endpoint.id ? updated : e)));
    try {
      await api.updateEndpoint(endpoint.id, { enabled: !endpoint.enabled });
    } catch {
      // Revert on error
      setEndpoints(endpoints);
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">端点配置</h1>
        <p className="text-sm text-gray-500">共 {endpoints.length} 个端点</p>
      </div>

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>路径</TableHead>
              <TableHead>方法</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>健康</TableHead>
              <TableHead>延迟</TableHead>
              <TableHead>启用</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {endpoints.map((endpoint) => (
              <TableRow key={endpoint.id}>
                <TableCell className="font-mono text-sm">{endpoint.path}</TableCell>
                <TableCell>
                  <Badge variant={endpoint.method === 'GET' ? 'info' : 'default'}>
                    {endpoint.method}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={endpoint.enabled ? 'success' : 'default'}>
                    {endpoint.enabled ? '已启用' : '已禁用'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={
                    endpoint.health_status === 'healthy' ? 'success' :
                    endpoint.health_status === 'degraded' ? 'warning' : 'error'
                  }>
                    {endpoint.health_status}
                  </Badge>
                </TableCell>
                <TableCell>{endpoint.latency_ms > 0 ? `${endpoint.latency_ms}ms` : '-'}</TableCell>
                <TableCell>
                  <Toggle checked={endpoint.enabled} onChange={() => handleToggle(endpoint)} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
