'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Toggle } from '@/components/ui/toggle';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';

// A model-provider endpoint as returned by the gateway's pool snapshot.
interface ModelEndpoint {
  id: string;
  provider: string;
  model: string;
  tier: string;
  enabled: boolean;
  health: string;
  weight: number;
  total_calls: number;
  total_failures: number;
  has_key: boolean;
}

export default function EndpointsPage() {
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadEndpoints();
  }, []);

  async function loadEndpoints() {
    setError(null);
    try {
      const data = await api.getEndpoints();
      setEndpoints((data || []) as unknown as ModelEndpoint[]);
    } catch (e) {
      // Honest failure — no fabricated endpoint list (audit F6).
      setEndpoints([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleToggle(endpoint: ModelEndpoint) {
    const next = !endpoint.enabled;
    setEndpoints((prev) => prev.map((e) => (e.id === endpoint.id ? { ...e, enabled: next } : e)));
    try {
      await api.updateEndpoint(endpoint.id, { enabled: next });
    } catch (e) {
      // Revert on real failure.
      setEndpoints((prev) => prev.map((ep) => (ep.id === endpoint.id ? { ...ep, enabled: !next } : ep)));
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const healthVariant = (h: string) =>
    h === 'healthy' ? 'success' : h === 'unhealthy' ? 'error' : 'warning';

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">模型端点配置</h1>
        <p className="text-sm text-gray-500">共 {endpoints.length} 个模型端点</p>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>层级</TableHead>
              <TableHead>健康</TableHead>
              <TableHead>权重</TableHead>
              <TableHead>调用/失败</TableHead>
              <TableHead>启用</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {endpoints.map((endpoint) => (
              <TableRow key={endpoint.id}>
                <TableCell className="font-mono text-sm">{endpoint.id}</TableCell>
                <TableCell>{endpoint.provider}</TableCell>
                <TableCell className="font-mono text-sm">{endpoint.model}</TableCell>
                <TableCell><Badge variant="info">{endpoint.tier}</Badge></TableCell>
                <TableCell>
                  <Badge variant={healthVariant(endpoint.health)}>{endpoint.health}</Badge>
                </TableCell>
                <TableCell>{endpoint.weight}</TableCell>
                <TableCell className="text-xs text-gray-500">
                  {endpoint.total_calls} / {endpoint.total_failures}
                </TableCell>
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
