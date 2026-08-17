'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Dialog } from '@/components/ui/dialog';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { maskKey, formatDate } from '@/lib/utils';
import type { ApiKey } from '@/types';

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  useEffect(() => {
    loadKeys();
  }, []);

  async function loadKeys() {
    setError(null);
    try {
      const data = await api.getApiKeys();
      setKeys((data || []) as unknown as ApiKey[]);
    } catch (e) {
      // Honest failure — no fabricated keys (audit F6).
      setKeys([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!newKeyName.trim()) return;
    setError(null);
    try {
      const res = (await api.createApiKey(newKeyName)) as { key?: string } | null;
      // The plaintext key is returned exactly once — surface it to the user.
      if (res && res.key) setCreatedKey(res.key);
      setDialogOpen(false);
      setNewKeyName('');
      await loadKeys();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('确认删除此 API Key？')) return;
    setError(null);
    try {
      await api.deleteApiKey(id);
      setKeys(keys.filter((k) => k.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">API Key 管理</h1>
        <Button onClick={() => setDialogOpen(true)}>+ 创建 Key</Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}
      {createdKey && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          <div className="font-medium mb-1">新 Key 已创建(明文仅显示一次,请妥善保存):</div>
          <code className="font-mono break-all">{createdKey}</code>
        </div>
      )}

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>Key</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>配额使用</TableHead>
              <TableHead>最后使用</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((key) => (
              <TableRow key={key.id}>
                <TableCell className="font-medium">{key.name}</TableCell>
                <TableCell className="font-mono text-sm">{maskKey(key.key)}</TableCell>
                <TableCell>
                  <Badge variant={key.status === 'active' ? 'success' : 'error'}>
                    {key.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="w-32">
                    {(() => {
                      const used = Number(key.used) || 0;
                      const quota = Number(key.quota) || 0;
                      const pct = quota > 0 ? (used / quota) * 100 : 0;
                      return (
                        <>
                          <div className="flex justify-between text-xs text-gray-500 mb-1">
                            <span>{pct.toFixed(0)}%</span>
                            <span>{used.toLocaleString()} / {quota.toLocaleString()}</span>
                          </div>
                          <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-blue-500'
                              }`}
                              style={{ width: `${Math.min(pct, 100)}%` }}
                            />
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </TableCell>
                <TableCell className="text-sm text-gray-500">
                  {key.last_used == null ? '从未使用' : formatDate(key.last_used)}
                </TableCell>
                <TableCell>
                  <Button variant="danger" size="sm" onClick={() => handleDelete(key.id)}>
                    删除
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} title="创建 API Key">
        <div className="space-y-4">
          <Input
            label="Key 名称"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="e.g. Production Key"
          />
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={handleCreate} disabled={!newKeyName.trim()}>创建</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
