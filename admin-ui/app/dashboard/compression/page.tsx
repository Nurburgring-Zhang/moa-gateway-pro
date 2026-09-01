'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';

interface CompressionModeInfo {
  name: string;
  description: string;
  is_default: boolean;
}

interface CompressionModeEnvelope {
  modes: CompressionModeInfo[];
  config?: Record<string, unknown>;
  stacked_default_pipeline?: Array<Record<string, unknown>>;
}

interface CompressionStatsEnvelope {
  started_at?: string | number;
  total_calls?: number;
  total_saved_chars?: number;
  modes?: Record<
    string,
    {
      calls?: number;
      original_chars?: number;
      saved_chars?: number;
      compressed_calls?: number;
      avg_savings_percent?: number;
      avg_duration_ms?: number;
    }
  >;
}

interface CompressTextResult {
  text?: string;
  compressed?: boolean;
  mode?: string;
  fidelity_score?: number;
  techniques_used?: string[];
  original_chars?: number;
  compressed_chars?: number;
}

export default function CompressionPage() {
  const [modes, setModes] = useState<CompressionModeInfo[]>([]);
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [stackedPipeline, setStackedPipeline] = useState<Array<Record<string, unknown>>>([]);
  const [stats, setStats] = useState<CompressionStatsEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [modesError, setModesError] = useState<string | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);

  const [text, setText] = useState('');
  const [mode, setMode] = useState('');
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [result, setResult] = useState<CompressTextResult | null>(null);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadAll() {
    setLoading(true);
    setModesError(null);
    setStatsError(null);
    try {
      const raw = (await api.getCompressionModes()) as CompressionModeEnvelope;
      const list = Array.isArray(raw?.modes) ? raw.modes : [];
      setModes(list);
      setConfig(raw?.config ?? null);
      setStackedPipeline(Array.isArray(raw?.stacked_default_pipeline) ? raw.stacked_default_pipeline : []);
      const def = list.find((m) => m.is_default);
      if (list.length > 0) setMode((prev) => prev || def?.name || list[0].name);
    } catch (e) {
      setModes([]);
      setModesError(e instanceof Error ? e.message : String(e));
    }
    try {
      setStats((await api.getCompressionStats()) as CompressionStatsEnvelope);
    } catch (e) {
      setStats(null);
      setStatsError(e instanceof Error ? e.message : String(e));
    }
    setLoading(false);
  }

  async function handleCompress() {
    setRunError(null);
    setResult(null);
    if (!text.trim()) {
      setRunError('请输入待压缩文本');
      return;
    }
    setBusy(true);
    try {
      const body = (await api.compressPrompt({ text, mode: mode || undefined })) as {
        result?: CompressTextResult;
      };
      setResult(body.result ?? null);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const originalChars = result?.original_chars ?? 0;
  const compressedChars = result?.compressed_chars ?? 0;
  const savingsPct =
    originalChars > 0 ? ((originalChars - compressedChars) / originalChars) * 100 : 0;
  const modeStats = stats?.modes ? Object.entries(stats.modes) : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">提示词压缩（v4.1）</h1>
        <div className="flex items-center gap-2">
          {config && (
            <Badge variant={config.enabled ? 'success' : 'warning'}>
              {config.enabled ? '已启用' : '已禁用'}
            </Badge>
          )}
          <Button variant="secondary" onClick={loadAll}>刷新</Button>
        </div>
      </div>

      {/* Mode catalogue */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5">
          <CardTitle>压缩模式（GET /v1/compression/modes）</CardTitle>
          {config && (
            <p className="text-sm text-gray-500 mt-1">
              默认模式：<code className="font-mono">{String(config.default_mode ?? '-')}</code>
              ，保真度门槛：<code className="font-mono">{String(config.fidelity_gate ?? '-')}</code>
              ，硬预算：<code className="font-mono">{String(config.hard_budget_chars ?? '-')} 字符</code>
              ，输入上限：<code className="font-mono">{String(config.max_input_chars ?? '-')} 字符</code>
            </p>
          )}
        </CardHeader>
        {modesError && (
          <div className="mx-6 mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {modesError}
          </div>
        )}
        {modes.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 p-6 pt-0">
            {modes.map((m) => (
              <div
                key={m.name}
                className={`rounded-lg border p-4 cursor-pointer transition-colors ${
                  mode === m.name ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-blue-300'
                }`}
                onClick={() => setMode(m.name)}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono font-medium">{m.name}</span>
                  <span className="flex gap-1">
                    {m.is_default && <Badge variant="success">默认</Badge>}
                    {mode === m.name && <Badge variant="info">已选</Badge>}
                  </span>
                </div>
                <p className="mt-2 text-sm text-gray-500">{m.description}</p>
              </div>
            ))}
          </div>
        ) : (
          <CardContent className="text-sm text-gray-500">
            后端未返回压缩模式（stacked_compression 能力未启用或模块未就绪）。
          </CardContent>
        )}
        {stackedPipeline.length > 0 && (
          <CardContent className="text-xs text-gray-500">
            stacked 默认流水线：
            {stackedPipeline
              .map((s) => `${String(s.engine ?? '?')}(${String(s.intensity ?? '?')})`)
              .join(' → ')}
          </CardContent>
        )}
      </Card>

      {/* Stats panel */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5">
          <CardTitle>压缩统计（GET /v1/compression/stats）</CardTitle>
          {stats && (
            <p className="text-sm text-gray-500 mt-1">
              自 {stats.started_at != null ? String(stats.started_at) : '-'} 起共{' '}
              {stats.total_calls ?? 0} 次调用，累计节省 {stats.total_saved_chars ?? 0} 字符。
            </p>
          )}
        </CardHeader>
        {statsError && (
          <div className="mx-6 mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {statsError}
          </div>
        )}
        {modeStats.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>模式</TableHead>
                <TableHead>调用次数</TableHead>
                <TableHead>原始字符</TableHead>
                <TableHead>节省字符</TableHead>
                <TableHead>平均节省率</TableHead>
                <TableHead>平均耗时</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {modeStats.map(([name, bucket]) => (
                <TableRow key={name}>
                  <TableCell className="font-mono">{name}</TableCell>
                  <TableCell>{bucket.calls ?? 0}</TableCell>
                  <TableCell>{(bucket.original_chars ?? 0).toLocaleString()}</TableCell>
                  <TableCell>{(bucket.saved_chars ?? 0).toLocaleString()}</TableCell>
                  <TableCell>
                    <Badge variant={(bucket.avg_savings_percent ?? 0) > 0 ? 'success' : 'default'}>
                      {(bucket.avg_savings_percent ?? 0).toFixed(2)}%
                    </Badge>
                  </TableCell>
                  <TableCell>{(bucket.avg_duration_ms ?? 0).toFixed(2)} ms</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            暂无压缩统计数据——进程启动后还没有执行过压缩。
          </CardContent>
        )}
      </Card>

      {/* Compression drill */}
      <Card>
        <CardHeader>
          <CardTitle>压缩演练（POST /v1/compression/compress）</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            提交文本与模式，后端走真实压缩流水线（保真度不达标会自动回退）。
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">待压缩文本</label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="粘贴需要压缩的提示词 / 工具输出…"
            />
          </div>
          <div className="flex items-end gap-3">
            <div className="w-64">
              <label className="block text-sm font-medium text-gray-700 mb-1">模式</label>
              {modes.length > 0 ? (
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value)}
                  className="w-full text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {modes.map((m) => (
                    <option key={m.name} value={m.name}>{m.name}</option>
                  ))}
                </select>
              ) : (
                <input
                  value={mode}
                  onChange={(e) => setMode(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="模式名（后端未提供列表时手填）"
                />
              )}
            </div>
            <Button onClick={handleCompress} disabled={busy || !text.trim()}>
              {busy ? '压缩中…' : '执行压缩'}
            </Button>
          </div>

          {runError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{runError}</div>
          )}

          {result && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge variant={result.compressed ? 'success' : 'default'}>
                  {result.compressed ? '已压缩' : '未压缩（直通）'}
                </Badge>
                {result.mode && <Badge variant="info">模式：{result.mode}</Badge>}
                {result.fidelity_score != null && (
                  <Badge variant={result.fidelity_score >= 0.9 ? 'success' : 'warning'}>
                    保真度：{result.fidelity_score.toFixed(3)}
                  </Badge>
                )}
                <Badge variant="default">
                  {originalChars.toLocaleString()} → {compressedChars.toLocaleString()} 字符
                </Badge>
                <Badge variant={savingsPct > 0 ? 'success' : 'default'}>
                  节省 {savingsPct.toFixed(1)}%
                </Badge>
              </div>
              {result.techniques_used && result.techniques_used.length > 0 && (
                <div className="text-xs text-gray-600">
                  <span className="font-medium">技术：</span>
                  {result.techniques_used.join('、')}
                </div>
              )}
              {typeof result.text === 'string' && (
                <pre className="max-h-72 overflow-auto rounded-md bg-white border border-blue-100 p-3 text-xs whitespace-pre-wrap break-all">
                  {result.text}
                </pre>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
