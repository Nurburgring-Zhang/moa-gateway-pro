'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Dialog } from '@/components/ui/dialog';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import type { FreeTierEntry } from '@/types';

const PAGE_SIZE = 50;

function fmtTokens(n: number | undefined): string {
  if (n == null) return '-';
  if (n === 0) return '0';
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// Resolve the catalogue key used by GET /v1/free-tiers/{key}.
function entryKey(entry: FreeTierEntry): string {
  if (typeof entry.key === 'string' && entry.key) return entry.key;
  if (typeof entry.poolKey === 'string' && entry.poolKey) return entry.poolKey;
  return `${entry.provider ?? ''}/${entry.modelId ?? ''}`;
}

export default function FreeTiersPage() {
  const [entries, setEntries] = useState<FreeTierEntry[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [provider, setProvider] = useState('');
  const [regime, setRegime] = useState('');
  const [appliedProvider, setAppliedProvider] = useState('');
  const [appliedRegime, setAppliedRegime] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailKey, setDetailKey] = useState('');
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  useEffect(() => {
    load(0, '', '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load(nextOffset: number, prov: string, reg: string) {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getFreeTiers({
        provider: prov || undefined,
        regime: reg || undefined,
        page: Math.floor(nextOffset / PAGE_SIZE) + 1,
        pageSize: PAGE_SIZE,
      });
      setEntries(res.entries);
      setTotal(res.total);
      setOffset(nextOffset);
    } catch (e) {
      setEntries([]);
      setTotal(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function applyFilters() {
    setAppliedProvider(provider.trim());
    setAppliedRegime(regime.trim());
    load(0, provider.trim(), regime.trim());
  }

  function clearFilters() {
    setProvider('');
    setRegime('');
    setAppliedProvider('');
    setAppliedRegime('');
    load(0, '', '');
  }

  async function openDetail(entry: FreeTierEntry) {
    const key = entryKey(entry);
    setDetailKey(key);
    setDetailOpen(true);
    setDetail(null);
    setDetailError(null);
    setDetailBusy(true);
    try {
      setDetail(await api.getFreeTier(key));
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailBusy(false);
    }
  }

  const knownTotal = total ?? null;
  const canPrev = offset > 0;
  const canNext = entries.length === PAGE_SIZE && (knownTotal == null || offset + PAGE_SIZE < knownTotal);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">免费模型目录（v4.1）</h1>
        {knownTotal != null && <Badge variant="info">共 {knownTotal} 条</Badge>}
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-1">
          <div className="w-56">
            <Input label="provider 过滤" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="如 openrouter" />
          </div>
          <div className="w-56">
            <Input label="regime 过滤" value={regime} onChange={(e) => setRegime(e.target.value)} placeholder="如 recurring-capped" />
          </div>
          <Button onClick={applyFilters}>查询</Button>
          <Button variant="secondary" onClick={clearFilters}>清除过滤</Button>
          {(appliedProvider || appliedRegime) && (
            <span className="text-sm text-gray-500">
              当前过滤：{appliedProvider || '全部 provider'} / {appliedRegime || '全部 regime'}
            </span>
          )}
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
          </div>
        ) : entries.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>模型</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Model ID</TableHead>
                <TableHead>免费机制</TableHead>
                <TableHead>月度 Token</TableHead>
                <TableHead>赠送 Token</TableHead>
                <TableHead>ToS</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry, idx) => (
                <TableRow key={`${entry.provider}/${entry.modelId}/${idx}`}>
                  <TableCell className="font-medium whitespace-normal">
                    {entry.displayName || entry.modelId || '-'}
                  </TableCell>
                  <TableCell>
                    <Badge variant="default">{entry.provider ?? '-'}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs whitespace-normal">{entry.modelId ?? '-'}</TableCell>
                  <TableCell>
                    <Badge variant="info">{entry.freeType ?? '-'}</Badge>
                  </TableCell>
                  <TableCell>{fmtTokens(entry.monthlyTokens)}</TableCell>
                  <TableCell>{fmtTokens(entry.creditTokens)}</TableCell>
                  <TableCell>
                    {entry.tos ? (
                      <Badge variant={entry.tos === 'ok' ? 'success' : entry.tos === 'caution' ? 'warning' : 'default'}>
                        {entry.tos}
                      </Badge>
                    ) : (
                      '-'
                    )}
                  </TableCell>
                  <TableCell>
                    <Button variant="secondary" size="sm" onClick={() => openDetail(entry)}>详情</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-gray-500">
            没有匹配的目录条目——调整过滤条件，或确认免费目录模块已加载。
          </CardContent>
        )}
      </Card>

      {/* Pagination */}
      {!loading && entries.length > 0 && (
        <div className="flex items-center justify-between">
          <Button variant="secondary" disabled={!canPrev} onClick={() => load(Math.max(0, offset - PAGE_SIZE), appliedProvider, appliedRegime)}>
            上一页
          </Button>
          <span className="text-sm text-gray-500">
            第 {offset + 1}–{offset + entries.length} 条{knownTotal != null ? ` / 共 ${knownTotal} 条` : ''}
          </span>
          <Button variant="secondary" disabled={!canNext} onClick={() => load(offset + PAGE_SIZE, appliedProvider, appliedRegime)}>
            下一页
          </Button>
        </div>
      )}

      {/* Detail dialog */}
      <Dialog open={detailOpen} onClose={() => setDetailOpen(false)} title={`目录条目：${detailKey}`}>
        {detailBusy && (
          <div className="flex items-center justify-center h-24">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600" />
          </div>
        )}
        {detailError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{detailError}</div>
        )}
        {detail && !detailBusy && (
          <div className="max-h-96 overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>字段</TableHead>
                  <TableHead>值</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(detail).map(([k, v]) => (
                  <TableRow key={k}>
                    <TableCell className="font-mono text-gray-600">{k}</TableCell>
                    <TableCell className="whitespace-normal break-all font-mono text-xs">
                      {v == null ? '-' : typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Dialog>
    </div>
  );
}
