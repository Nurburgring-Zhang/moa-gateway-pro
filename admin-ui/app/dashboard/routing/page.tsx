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
import type {
  RoutingResolveResponse,
  RoutingStrategiesResponse,
  RoutingTelemetryResponse,
} from '@/types';

// One editable candidate row in the dry-run form. Mirrors the backend
// EndpointCandidate fields used for ranking (typos fail loudly upstream).
interface CandidateForm {
  endpoint_id: string;
  provider: string;
  model_id: string;
  weight: string;
  priority: string;
  latency_p95_ms: string;
  success_rate: string;
  quota_remaining_pct: string;
}

function emptyCandidate(): CandidateForm {
  return {
    endpoint_id: '',
    provider: '',
    model_id: '',
    weight: '100',
    priority: '0',
    latency_p95_ms: '0',
    success_rate: '1',
    quota_remaining_pct: '100',
  };
}

function numOrUndefined(value: string): number | undefined {
  if (value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

export default function RoutingPage() {
  const [strategies, setStrategies] = useState<RoutingStrategiesResponse | null>(null);
  const [telemetry, setTelemetry] = useState<RoutingTelemetryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [telemetryError, setTelemetryError] = useState<string | null>(null);

  // dry-run form state
  const [strategy, setStrategy] = useState('');
  const [sessionKey, setSessionKey] = useState('');
  const [groupKey, setGroupKey] = useState('default');
  const [taskType, setTaskType] = useState('default');
  const [model, setModel] = useState('');
  const [candidates, setCandidates] = useState<CandidateForm[]>([emptyCandidate(), emptyCandidate()]);
  const [resolveBusy, setResolveBusy] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [decision, setDecision] = useState<RoutingResolveResponse | null>(null);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getRoutingStrategies();
      setStrategies(data);
    } catch (e) {
      // Honest failure — no fabricated strategy catalogue.
      setStrategies(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    await loadTelemetry();
  }

  async function loadTelemetry() {
    setTelemetryError(null);
    try {
      setTelemetry(await api.getRoutingTelemetry());
    } catch (e) {
      setTelemetry(null);
      setTelemetryError(e instanceof Error ? e.message : String(e));
    }
  }

  function updateCandidate(index: number, patch: Partial<CandidateForm>) {
    setCandidates((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  async function handleResolve() {
    setResolveError(null);
    setDecision(null);
    const valid = candidates.filter((c) => c.endpoint_id.trim() !== '');
    if (valid.length === 0) {
      setResolveError('请至少填写一个候选端点（endpoint_id 必填）');
      return;
    }
    setResolveBusy(true);
    try {
      const payload = {
        candidates: valid.map((c) => {
          const row: Record<string, unknown> = { endpoint_id: c.endpoint_id.trim() };
          if (c.provider.trim()) row.provider = c.provider.trim();
          if (c.model_id.trim()) row.model_id = c.model_id.trim();
          const weight = numOrUndefined(c.weight);
          if (weight !== undefined) row.weight = weight;
          const priority = numOrUndefined(c.priority);
          if (priority !== undefined) row.priority = priority;
          const p95 = numOrUndefined(c.latency_p95_ms);
          if (p95 !== undefined) row.latency_p95_ms = p95;
          const sr = numOrUndefined(c.success_rate);
          if (sr !== undefined) row.success_rate = sr;
          const quota = numOrUndefined(c.quota_remaining_pct);
          if (quota !== undefined) row.quota_remaining_pct = quota;
          return row;
        }),
        strategy: strategy || null,
        context: {
          group_key: groupKey.trim() || 'default',
          task_type: taskType.trim() || 'default',
          ...(sessionKey.trim() ? { session_key: sessionKey.trim() } : {}),
          ...(model.trim() ? { model: model.trim() } : {}),
        },
        dry_run: true,
      };
      setDecision(await api.resolveRouting(payload));
    } catch (e) {
      setResolveError(e instanceof Error ? e.message : String(e));
    } finally {
      setResolveBusy(false);
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
        <h1 className="text-2xl font-bold text-gray-900">路由策略（v4.1）</h1>
        <div className="flex items-center gap-2">
          {strategies && (
            <>
              <Badge variant={strategies.enabled ? 'success' : 'warning'}>
                {strategies.enabled ? '已启用' : '已禁用'}
              </Badge>
              <Badge variant="info">默认策略：{strategies.default_strategy}</Badge>
              <Badge variant="default">{strategies.count} 个策略</Badge>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Strategy catalogue */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5">
          <CardTitle>策略目录</CardTitle>
        </CardHeader>
        {strategies && strategies.strategies.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>策略</TableHead>
                <TableHead>模式</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>说明</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {strategies.strategies.map((s) => (
                <TableRow key={s.name}>
                  <TableCell className="font-mono font-medium">
                    {s.name}
                    {strategies.default_strategy === s.name && (
                      <Badge variant="info" className="ml-2">默认</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="default">{s.mode}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={s.internal ? 'warning' : 'success'}>
                      {s.internal ? 'internal' : 'public'}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-w-xl whitespace-normal text-gray-600">
                    {s.description}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            后端未返回任何策略（可能路由模块已禁用或请求失败）。
          </CardContent>
        )}
      </Card>

      {/* Dry-run ranking drill */}
      <Card>
        <CardHeader>
          <CardTitle>Dry-run 排序演练</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            向 POST /v1/routing/resolve 提交候选池与上下文，仅演练排序，不会改变任何计数器或 inflight 状态。
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">策略</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">（使用网关默认）</option>
                {(strategies?.strategies ?? []).map((s) => (
                  <option key={s.name} value={s.name}>{s.name}</option>
                ))}
              </select>
            </div>
            <Input label="session_key" value={sessionKey} onChange={(e) => setSessionKey(e.target.value)} placeholder="可选" />
            <Input label="group_key" value={groupKey} onChange={(e) => setGroupKey(e.target.value)} />
            <Input label="task_type" value={taskType} onChange={(e) => setTaskType(e.target.value)} />
            <Input label="model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="可选" />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-700">候选端点</span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setCandidates((prev) => [...prev, emptyCandidate()])}
              >
                + 添加候选
              </Button>
            </div>
            {candidates.map((c, i) => (
              <div key={i} className="grid grid-cols-1 md:grid-cols-9 gap-2 items-end rounded-lg border border-gray-200 p-3">
                <div className="md:col-span-2">
                  <Input label={i === 0 ? 'endpoint_id *' : undefined} value={c.endpoint_id} onChange={(e) => updateCandidate(i, { endpoint_id: e.target.value })} placeholder="ep-xxx" />
                </div>
                <Input label={i === 0 ? 'provider' : undefined} value={c.provider} onChange={(e) => updateCandidate(i, { provider: e.target.value })} />
                <Input label={i === 0 ? 'model_id' : undefined} value={c.model_id} onChange={(e) => updateCandidate(i, { model_id: e.target.value })} />
                <Input label={i === 0 ? 'weight' : undefined} value={c.weight} onChange={(e) => updateCandidate(i, { weight: e.target.value })} />
                <Input label={i === 0 ? 'p95(ms)' : undefined} value={c.latency_p95_ms} onChange={(e) => updateCandidate(i, { latency_p95_ms: e.target.value })} />
                <Input label={i === 0 ? 'success' : undefined} value={c.success_rate} onChange={(e) => updateCandidate(i, { success_rate: e.target.value })} />
                <Input label={i === 0 ? 'quota%' : undefined} value={c.quota_remaining_pct} onChange={(e) => updateCandidate(i, { quota_remaining_pct: e.target.value })} />
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setCandidates((prev) => prev.filter((_, j) => j !== i))}
                  disabled={candidates.length <= 1}
                >
                  移除
                </Button>
              </div>
            ))}
          </div>

          {resolveError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {resolveError}
            </div>
          )}

          <div className="flex justify-end">
            <Button onClick={handleResolve} disabled={resolveBusy}>
              {resolveBusy ? '演练中…' : '执行 Dry-run'}
            </Button>
          </div>

          {decision && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="info">策略：{decision.strategy}</Badge>
                <Badge variant="default">模式：{decision.mode}</Badge>
                <Badge variant="success">选中：{decision.selected ?? '（无）'}</Badge>
                <Badge variant="default">候选数：{decision.candidate_count}</Badge>
                <Badge variant="warning">dry_run</Badge>
              </div>
              <div className="text-sm text-gray-700">
                <span className="font-medium">回退链：</span>
                {decision.ordered.length > 0
                  ? decision.ordered.map((id, idx) => (
                      <span key={id} className="font-mono">
                        {idx > 0 && <span className="text-gray-400 mx-1">→</span>}
                        {id}
                      </span>
                    ))
                  : '（空）'}
              </div>
              {decision.selections.length > 0 && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>排名</TableHead>
                      <TableHead>端点</TableHead>
                      <TableHead>得分</TableHead>
                      <TableHead>理由</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {decision.selections.map((sel) => (
                      <TableRow key={`${sel.rank}-${sel.endpoint_id}`}>
                        <TableCell>{sel.rank}</TableCell>
                        <TableCell className="font-mono">{sel.endpoint_id}</TableCell>
                        <TableCell>{sel.score == null ? '-' : sel.score.toFixed(3)}</TableCell>
                        <TableCell className="max-w-md whitespace-normal text-gray-600">{sel.reason}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              {Object.keys(decision.scores).length > 0 && (
                <div className="text-xs text-gray-600">
                  <span className="font-medium">scores：</span>
                  {Object.entries(decision.scores).map(([k, v]) => `${k}=${v.toFixed(3)}`).join('，')}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Telemetry */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5 flex flex-row items-center justify-between">
          <CardTitle>端点遥测（滚动窗口 {telemetry?.history_window ?? '-'}）</CardTitle>
          <Button variant="secondary" size="sm" onClick={loadTelemetry}>刷新</Button>
        </CardHeader>
        {telemetryError && (
          <div className="mx-6 mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {telemetryError}
          </div>
        )}
        {telemetry && telemetry.endpoints.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>端点</TableHead>
                <TableHead>请求总数</TableHead>
                <TableHead>窗口样本</TableHead>
                <TableHead>平均延迟</TableHead>
                <TableHead>P95 延迟</TableHead>
                <TableHead>标准差</TableHead>
                <TableHead>成功率</TableHead>
                <TableHead>错误率</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {telemetry.endpoints.map((ep) => (
                <TableRow key={ep.endpoint_id}>
                  <TableCell className="font-mono">{ep.endpoint_id}</TableCell>
                  <TableCell>{ep.request_count}</TableCell>
                  <TableCell>{ep.window_count}</TableCell>
                  <TableCell>{ep.avg_latency_ms.toFixed(1)} ms</TableCell>
                  <TableCell>{ep.p95_latency_ms.toFixed(1)} ms</TableCell>
                  <TableCell>{ep.stddev_latency_ms.toFixed(1)} ms</TableCell>
                  <TableCell>
                    <Badge variant={ep.success_rate >= 0.95 ? 'success' : ep.success_rate >= 0.8 ? 'warning' : 'error'}>
                      {(ep.success_rate * 100).toFixed(1)}%
                    </Badge>
                  </TableCell>
                  <TableCell>{(ep.error_rate * 100).toFixed(1)}%</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            暂无遥测数据——尚未有请求经过路由策略引擎，或遥测接口不可用。
          </CardContent>
        )}
      </Card>
    </div>
  );
}
