'use client';

import { useState } from 'react';
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
import type { MemoryItem, MemoryRecallResponse } from '@/types';

const PAGE_SIZE = 20;

// Memory types accepted by GET /v1/memory/items (backend classifier enum).
const MEMORY_TYPES = ['', 'core', 'episodic', 'semantic', 'procedural', 'unclassified'];

function typeVariant(t: string): 'info' | 'success' | 'warning' | 'default' {
  switch (t) {
    case 'core':
      return 'success';
    case 'episodic':
      return 'info';
    case 'procedural':
      return 'warning';
    default:
      return 'default';
  }
}

export default function MemoryPage() {
  const [repository, setRepository] = useState('');
  const [memoryType, setMemoryType] = useState('');
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [recallQuery, setRecallQuery] = useState('');
  const [recallRepo, setRecallRepo] = useState('');
  const [recallBusy, setRecallBusy] = useState(false);
  const [recallResult, setRecallResult] = useState<MemoryRecallResponse | null>(null);
  const [recallError, setRecallError] = useState<string | null>(null);

  async function load(nextOffset: number, repo: string, mtype: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getMemoryItems({
        repository: repo,
        memoryType: mtype || undefined,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(res.items ?? []);
      setTotal(typeof res.total === 'number' ? res.total : null);
      setOffset(nextOffset);
      setLoaded(true);
    } catch (e) {
      setItems([]);
      setTotal(null);
      setLoaded(true);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function applyQuery() {
    if (!repository.trim()) {
      setError('repository 为必填项——记忆按仓库作用域隔离');
      return;
    }
    load(0, repository.trim(), memoryType);
  }

  async function handleDelete(item: MemoryItem) {
    if (!confirm(`确认删除记忆条目 #${item.id}？此操作不可恢复。`)) return;
    setError(null);
    try {
      await api.deleteMemoryItem(item.id, repository.trim());
      await load(offset, repository.trim(), memoryType);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleRecall() {
    setRecallError(null);
    setRecallResult(null);
    if (!recallQuery.trim()) {
      setRecallError('请输入召回查询文本');
      return;
    }
    setRecallBusy(true);
    try {
      setRecallResult(
        await api.recallMemory({
          query: recallQuery.trim(),
          repository: recallRepo.trim() || undefined,
        })
      );
    } catch (e) {
      setRecallError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecallBusy(false);
    }
  }

  const canPrev = offset > 0;
  const canNext = items.length === PAGE_SIZE && (total == null || offset + PAGE_SIZE < total);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">记忆系统（v4.1）</h1>

      {/* Recall tester */}
      <Card>
        <CardHeader>
          <CardTitle>召回测试器（GET /v1/memory/recall）</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            对指定作用域执行一次混合召回，验证检索链路是否工作（受 retrieval_enabled 配置约束）。
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-64">
              <Input label="查询文本" value={recallQuery} onChange={(e) => setRecallQuery(e.target.value)} placeholder="要召回的内容关键词" />
            </div>
            <div className="w-56">
              <Input label="repository（可选）" value={recallRepo} onChange={(e) => setRecallRepo(e.target.value)} placeholder="仓库作用域" />
            </div>
            <Button onClick={handleRecall} disabled={recallBusy || !recallQuery.trim()}>
              {recallBusy ? '召回中…' : '执行召回'}
            </Button>
          </div>
          {recallError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{recallError}</div>
          )}
          {recallResult && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-2">
              <div className="flex flex-wrap gap-2">
                <Badge variant={recallResult.retrieved ? 'success' : 'default'}>
                  {recallResult.retrieved ? '命中' : '未命中'}
                </Badge>
                <Badge variant="info">{recallResult.item_count} 条</Badge>
                {recallResult.latency_ms != null && (
                  <Badge variant="default">{recallResult.latency_ms} ms</Badge>
                )}
                {recallResult.backend && <Badge variant="default">backend: {recallResult.backend}</Badge>}
                {recallResult.skip_reason && (
                  <Badge variant="warning">skip: {recallResult.skip_reason}</Badge>
                )}
              </div>
              {recallResult.context && (
                <pre className="max-h-48 overflow-auto rounded-md bg-white border border-blue-100 p-3 text-xs whitespace-pre-wrap break-all">
                  {recallResult.context}
                </pre>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Item browser */}
      <Card className="p-0 overflow-hidden">
        <CardHeader className="px-6 pt-5">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-56">
              <Input label="repository（必填）" value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="记忆空间作用域" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">记忆类型</label>
              <select
                value={memoryType}
                onChange={(e) => setMemoryType(e.target.value)}
                className="text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {MEMORY_TYPES.map((t) => (
                  <option key={t} value={t}>{t || '全部类型'}</option>
                ))}
              </select>
            </div>
            <Button onClick={applyQuery} disabled={loading || !repository.trim()}>
              {loading ? '加载中…' : '查询条目'}
            </Button>
            {total != null && <Badge variant="info">共 {total} 条</Badge>}
          </div>
        </CardHeader>

        {error && (
          <div className="mx-6 mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {items.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>内容</TableHead>
                <TableHead>角色 / 来源</TableHead>
                <TableHead>会话</TableHead>
                <TableHead>更新时间</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => (
                <TableRow key={item.id}>
                  <TableCell className="text-xs text-gray-500">{item.id}</TableCell>
                  <TableCell>
                    <Badge variant={typeVariant(item.memory_type)}>{item.memory_type}</Badge>
                    {item.chunk_count > 1 && (
                      <span className="ml-1 text-xs text-gray-400">
                        {item.chunk_index + 1}/{item.chunk_count}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="max-w-xl whitespace-normal text-gray-700">
                    {item.content}
                  </TableCell>
                  <TableCell className="text-xs text-gray-500">
                    {item.role || '-'} / {item.source || '-'}
                  </TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal max-w-40">
                    {item.session_id || '-'}
                  </TableCell>
                  <TableCell className="text-xs text-gray-500">{formatDate(item.updated_at)}</TableCell>
                  <TableCell>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(item)}>删除</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            {!loaded
              ? '输入 repository 后点击「查询条目」浏览该作用域下的记忆。'
              : '该作用域下暂无记忆条目（或过滤条件过严）。'}
          </CardContent>
        )}
      </Card>

      {/* Pagination */}
      {loaded && items.length > 0 && (
        <div className="flex items-center justify-between">
          <Button variant="secondary" disabled={!canPrev || loading} onClick={() => load(Math.max(0, offset - PAGE_SIZE), repository.trim(), memoryType)}>
            上一页
          </Button>
          <span className="text-sm text-gray-500">
            第 {offset + 1}–{offset + items.length} 条{total != null ? ` / 共 ${total} 条` : ''}
          </span>
          <Button variant="secondary" disabled={!canNext || loading} onClick={() => load(offset + PAGE_SIZE, repository.trim(), memoryType)}>
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}
