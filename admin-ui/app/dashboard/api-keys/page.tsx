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

  useEffect(() => {
    loadKeys();
  }, []);

  async function loadKeys() {
    try {
      const data = await api.getApiKeys();
      setKeys(data as unknown as ApiKey[]);
    } catch {
      setKeys([
        { id: '1', name: 'Production Key', key: 'sk-moa-prod-abc123def456', quota: 1000000, used: 456789, created_at: '2024-01-01', last_used: '2024-08-01T09:30:00Z', status: 'active' },
        { id: '2', name: 'Development Key', key: 'sk-moa-dev-xyz789ghi012', quota: 100000, used: 23456, created_at: '2024-03-15', last_used: '2024-08-01T10:00:00Z', status: 'active' },
        { id: '3', name: 'Testing Key', key: 'sk-moa-test-jkl345mno678', quota: 50000, used: 1200, created_at: '2024-06-01', last_used: '2024-07-28T14:00:00Z', status: 'active' },
        { id: '4', name: 'Legacy Key', key: 'sk-moa-old-pqr901stu234', quota: 500000, used: 499000, created_at: '2023-06-01', last_used: '2024-05-01T00:00:00Z', status: 'revoked' },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!newKeyName.trim()) return;
    try {
      await api.createApiKey(newKeyName);
      await loadKeys();
    } catch {
      // Demo: add mock
      const newKey: ApiKey = {
        id: String(Date.now()),
        name: newKeyName,
        key: `sk-moa-new-${Math.random().toString(36).slice(2, 14)}`,
        quota: 100000,
        used: 0,
        created_at: new Date().toISOString(),
        last_used: '-',
        status: 'active',
      };
      setKeys([newKey, ...keys]);
    }
    setDialogOpen(false);
    setNewKeyName('');
  }

  async function handleDelete(id: string) {
    if (!confirm('确认删除此 API Key？')) return;
    try {
      await api.deleteApiKey(id);
    } catch {
      // Demo
    }
    setKeys(keys.filter((k) => k.id !== id));
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
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>{((key.used / key.quota) * 100).toFixed(0)}%</span>
                      <span>{key.used.toLocaleString()} / {key.quota.toLocaleString()}</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          key.used / key.quota > 0.9 ? 'bg-red-500' :
                          key.used / key.quota > 0.7 ? 'bg-yellow-500' : 'bg-blue-500'
                        }`}
                        style={{ width: `${Math.min((key.used / key.quota) * 100, 100)}%` }}
                      />
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-sm text-gray-500">
                  {key.last_used !== '-' ? formatDate(key.last_used) : '-'}
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
