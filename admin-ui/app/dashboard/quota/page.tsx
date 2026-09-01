'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { formatDate } from '@/lib/utils';
import type {
  QuotaCheckResponse,
  QuotaSnapshotRow,
  QuotaEndpointSummary,
  QuotaStatusResponse,
  QuotaValueInfo,
} from '@/types';

function statusVariant(status: string): 'success' | 'warning' | 'error' | 'default' {
  switch (status) {
    case 'healthy':
    case 'ok':
      return 'success';
    case 'approaching':
    case 'warn':
    case 'warning':
      return 'warning';
    case 'exhausted':
      return 'error';
    default:
      return 'default';
  }
}

function fmtValue(v: number | null | undefined): string {
  if (v == null) return '-';
  if (!Number.isFinite(v)) return String(v);
  return v.toLocaleString();
}

function QuotaValues({ values }: { values: QuotaValueInfo[] }) {
  if (!values || values.length === 0) {
    return <span className="text-gray-400 text-xs">无观测值</span>;
  }
  return (
    <div className="space-y-1">
      {values.map((v, i) => (
        <div key={i} className="text-xs text-gray-600 font-mono">
          {v.dimension ?? '?'}：used {fmtValue(v.used)}
          {v.limit != null ? ` / ${fmtValue(v.limit)}` : ''}
          {v.remaining != null ? `，剩余 ${fmtValue(v.remaining)}` : ''}
          {v.unit ? ` ${v.unit}` : ''}
          {v.reset_at ? `，重置 ${formatDate(v.reset_at)}` : ''}
          {v.confidence && v.confidence !== 'unknown' ? `（${v.confidence}）` : ''}
        </div>
      ))}
    </div>
  );
}

export default function QuotaPage() {
  const [status, setStatus] = useState<QuotaStatusResponse | null>(null);
  const [snapshots, setSnapshots] = useState<QuotaSnapshotRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  const [snapshotFilter, setSnapshotFilter] = useState('');
  const [checkForm, setCheckForm] = useState({ provider_id: '', connection_id: '', endpoint_id: '' });
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkResult, setCheckResult] = useState<QuotaCheckResponse | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshResult, setRefreshResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      setStatus(await api.getQuotaStatus());
    } catch (e) {
      setStatus(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    await loadSnapshots();
  }

  async function loadSnapshots(endpointId?: string) {
    setSnapshotError(null);
    try {
      const body = await api.getQuotaSnapshots(endpointId || undefined, 100);
      setSnapshots((body.snapshots ?? []) as unknown as QuotaSnapshotRow[]);
    } catch (e) {
      setSnapshots([]);
      setSnapshotError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleCheck() {
    setCheckError(null);
    setCheckResult(null);
    setCheckBusy(true);
    try {
      setCheckResult(await api.checkQuota(checkForm));
    } catch (e) {
      setCheckError(e instanceof Error ? e.message : String(e));
    } finally {
      setCheckBusy(false);
    }
  }

  async function handleRefresh() {
    setError(null);
    setRefreshResult(null);
    setRefreshBusy(true);
    try {
      setRefreshResult(await api.refreshQuota());
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">配额调度（v4.1）</h1>
        <Button onClick={handleRefresh} disabled={refreshBusy}>
          {refreshBusy ? '刷新中…' : '手动刷新（丢弃已翻滚窗口）'}
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}
      {refreshResult && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          刷新完成：
          <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-xs">{JSON.stringify(refreshResult)}</pre>
        </div>
      )}

      {/* Status cards */}
      {status && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <Card className="p-4">
            <div className="text-xs text-gray-500">调度器</div>
            <div className="mt-1">
              <Badge variant={status.enabled ? 'success' : 'warning'}>
                {status.enabled ? '已启用' : '已禁用'}
              </Badge>
            </div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-gray-500">失败策略</div>
            <div className="mt-1">
              <Badge variant={status.fail_open ? 'info' : 'error'}>
                {status.fail_open ? 'fail-open' : 'fail-closed'}
              </Badge>
            </div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-gray-500">跟踪端点数</div>
            <div className="mt-1 text-xl font-semibold">{status.endpoint_count}</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-gray-500">轮询间隔</div>
            <div className="mt-1 text-sm font-medium">
              {status.poll_interval_s}s / 快速 {status.fast_poll_interval_s}s
            </div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-gray-500">预警阈值</div>
            <div className="mt-1 text-xl font-semibold">{status.warn_threshold}</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs text-gray-500">耗尽阈值</div>
            <div className="mt-1 text-xl font-semibold">{status.exhaust_threshold}</div>
          </Card>
        </div>
      )}

      {/* Live endpoint quota states */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5 flex flex-row items-center justify-between">
          <CardTitle>端点配额状态</CardTitle>
          <Button variant="secondary" size="sm" onClick={loadAll}>刷新</Button>
        </CardHeader>
        {status && status.endpoints.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>端点</TableHead>
                <TableHead>Provider / Connection</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>观测值</TableHead>
                <TableHead>采集时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {status.endpoints.map((ep: QuotaEndpointSummary) => (
                <TableRow key={`${ep.provider_id}/${ep.connection_id}/${ep.endpoint_id}`}>
                  <TableCell className="font-mono">{ep.endpoint_id || '-'}</TableCell>
                  <TableCell className="text-xs text-gray-500">
                    {ep.provider_id || '-'} / {ep.connection_id || '-'}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(ep.status)}>{ep.status}</Badge>
                    {!ep.supported && (
                      <Badge variant="default" className="ml-1">不支持配额</Badge>
                    )}
                    {ep.error && (
                      <div className="mt-1 text-xs text-red-600 whitespace-normal max-w-xs">{ep.error}</div>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <QuotaValues values={ep.values} />
                  </TableCell>
                  <TableCell className="text-xs text-gray-500">
                    {ep.fetched_at ? formatDate(ep.fetched_at) : '-'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            暂无端点配额状态——尚未采集到任何配额观测值。
          </CardContent>
        )}
      </Card>

      {/* Can-afford check */}
      <Card>
        <CardHeader>
          <CardTitle>Can-afford 准入检查</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            调用 POST /v1/quota/check，判断某端点当前是否还允许接收请求（不确定时遵循 fail-open 配置）。
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Input label="provider_id" value={checkForm.provider_id} onChange={(e) => setCheckForm({ ...checkForm, provider_id: e.target.value })} placeholder="可选" />
            <Input label="connection_id" value={checkForm.connection_id} onChange={(e) => setCheckForm({ ...checkForm, connection_id: e.target.value })} placeholder="可选" />
            <Input label="endpoint_id" value={checkForm.endpoint_id} onChange={(e) => setCheckForm({ ...checkForm, endpoint_id: e.target.value })} placeholder="可选" />
          </div>
          {checkError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{checkError}</div>
          )}
          {checkResult && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 flex flex-wrap items-center gap-2 text-sm">
              <Badge variant={checkResult.allowed ? 'success' : 'error'}>
                {checkResult.allowed ? '允许通过' : '拒绝'}
              </Badge>
              <Badge variant="default">reason: {checkResult.reason}</Badge>
              <Badge variant={statusVariant(checkResult.status)}>status: {checkResult.status}</Badge>
              {checkResult.retry_after_ms != null && (
                <Badge variant="warning">retry_after: {Math.ceil(checkResult.retry_after_ms / 1000)}s</Badge>
              )}
            </div>
          )}
          <div className="flex justify-end">
            <Button onClick={handleCheck} disabled={checkBusy}>{checkBusy ? '检查中…' : '执行检查'}</Button>
          </div>
        </CardContent>
      </Card>

      {/* Snapshot history */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5 flex flex-row items-center justify-between gap-3">
          <CardTitle>快照历史（变更检测持久化）</CardTitle>
          <div className="flex items-center gap-2">
            <div className="w-64">
              <Input
                value={snapshotFilter}
                onChange={(e) => setSnapshotFilter(e.target.value)}
                placeholder="按 endpoint_id 过滤（留空 = 全部）"
              />
            </div>
            <Button variant="secondary" size="sm" onClick={() => loadSnapshots(snapshotFilter.trim())}>
              查询
            </Button>
          </div>
        </CardHeader>
        {snapshotError && (
          <div className="mx-6 mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {snapshotError}
          </div>
        )}
        {snapshots.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>采集时间</TableHead>
                <TableHead>端点</TableHead>
                <TableHead>Provider / Connection</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>观测值</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {snapshots.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="text-xs text-gray-500">{row.id}</TableCell>
                  <TableCell className="text-xs">
                    {row.captured_at ? formatDate(row.captured_at) : '-'}
                  </TableCell>
                  <TableCell className="font-mono">{row.endpoint_id || '-'}</TableCell>
                  <TableCell className="text-xs text-gray-500">
                    {row.provider_id || '-'} / {row.connection_id || '-'}
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <QuotaValues values={row.values ?? []} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            暂无快照记录——配额状态尚未发生过被持久化的变更。
          </CardContent>
        )}
      </Card>
    </div>
  );
}
