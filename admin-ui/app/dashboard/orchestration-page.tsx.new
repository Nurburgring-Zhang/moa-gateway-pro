'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface TraceItem {
  step_id: string;
  capability_id: string;
  ok: boolean;
  summary?: string;
}

interface OrchestratorResult {
  ok?: boolean;
  plan?: { steps: Array<{ step_id: string; capability_id: string; type: string; depends_on: string[]; title?: string }>; rationale?: string; plan_mode?: string };
  profile?: { complexity?: number; needs_moa?: boolean; mode?: string; is_composite?: boolean };
  execution?: { steps_ok?: number; steps_total?: number; trace?: TraceItem[] };
  reinforced_capabilities?: number;
  latency_ms?: number;
}

export default function OrchestrationPage() {
  const [task, setTask] = useState('');
  const [inputJson, setInputJson] = useState('{}');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<OrchestratorResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [caps, setCaps] = useState<{ total?: number; by_type?: Record<string, number> } | null>(null);

  useEffect(() => {
    loadCapabilities();
  }, []);

  async function loadCapabilities() {
    try {
      const c = await api.getOrchestratorCapabilities();
      setCaps({ total: c.total as number, by_type: c.by_type as Record<string, number> });
    } catch (e) {
      // 诚实: 目录加载失败仅提示, 不伪造能力清单
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleRun() {
    if (!task.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      let input: Record<string, unknown> = {};
      try {
        input = JSON.parse(inputJson || '{}');
      } catch {
        setError('任务输入不是合法 JSON, 已按空输入执行');
        input = {};
      }
      const res = await api.runOrchestration(task, input);
      setResult(res as unknown as OrchestratorResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">智能编排 (Autonomous Orchestration)</h1>
        {caps && (
          <Badge variant="info">能力总数 {caps.total ?? 0}</Badge>
        )}
      </div>

      {caps?.by_type && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(caps.by_type).map(([t, n]) => (
            <Badge key={t} variant="default">{t}: {n}</Badge>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <Card>
        <CardHeader><CardTitle>任务</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <textarea
            className="w-full border border-gray-300 rounded-lg p-3 text-sm min-h-[80px]"
            placeholder="描述一个复合任务, 例如: search the web for X, then compute Y using code, and analyze data ..."
            value={task}
            onChange={(e) => setTask(e.target.value)}
          />
          <input
            className="w-full border border-gray-300 rounded-lg p-2 text-sm font-mono"
            placeholder='任务输入 JSON, 例如 {"code":"print(1+2)","data":"1,2,3"}'
            value={inputJson}
            onChange={(e) => setInputJson(e.target.value)}
          />
          <Button onClick={handleRun} disabled={running || !task.trim()}>
            {running ? '编排执行中...' : '全自动编排执行'}
          </Button>
        </CardContent>
      </Card>

      {result && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>
                计划与执行{' '}
                <Badge variant={result.ok ? 'success' : 'error'}>{result.ok ? '成功' : '失败'}</Badge>{' '}
                {typeof result.latency_ms === 'number' && <Badge variant="default">{result.latency_ms}ms</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <span className="font-medium">规划模式: </span>{result.plan?.plan_mode ?? '-'}
                <span className="ml-4 font-medium">复杂度: </span>{result.profile?.complexity ?? '-'}
                <span className="ml-4 font-medium">需MoA: </span>{result.profile?.needs_moa ? '是' : '否'}
                <span className="ml-4 font-medium">强化能力数: </span>{result.reinforced_capabilities ?? 0}
              </div>
              <div className="text-gray-600">理由: {result.plan?.rationale ?? '-'}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>能力调用 Trace ({result.execution?.steps_ok}/{result.execution?.steps_total} 成功)</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {(result.execution?.trace ?? []).map((t) => (
                <div key={t.step_id} className="flex items-start gap-2 text-sm border-b border-gray-100 pb-2">
                  <Badge variant={t.ok ? 'success' : 'error'}>{t.ok ? 'OK' : 'FAIL'}</Badge>
                  <span className="font-mono text-xs mt-0.5">{t.capability_id}</span>
                  <span className="text-gray-600 break-all">{t.summary ?? ''}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
