'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Dialog } from '@/components/ui/dialog';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import type { Model } from '@/types';

export default function ModelsPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editModel, setEditModel] = useState<Partial<Model> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadModels();
  }, []);

  async function loadModels() {
    setError(null);
    try {
      const res = await api.getModels();
      setModels((res.data || []) as unknown as Model[]);
    } catch (e) {
      // Honest failure — no fabricated model list (audit F6).
      setModels([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function handleAdd() {
    setEditModel({ name: '', provider: '', weight: 100, status: 'active' });
    setDialogOpen(true);
  }

  function handleEdit(model: Model) {
    setEditModel(model);
    setDialogOpen(true);
  }

  async function handleSave() {
    if (!editModel) return;
    try {
      if (editModel.id) {
        await api.updateModel(editModel.id, editModel);
      } else {
        await api.createModel(editModel);
      }
      setDialogOpen(false);
      setEditModel(null);
      await loadModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(model: Model) {
    if (!window.confirm(`确认删除模型 "${model.name}"?`)) return;
    try {
      await api.deleteModel(String(model.id ?? model.name));
      await loadModels();
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
        <h1 className="text-2xl font-bold text-gray-900">模型管理</h1>
        <Button onClick={handleAdd}>+ 添加模型</Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>模型名称</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>权重</TableHead>
              <TableHead>能力</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {models.map((model) => (
              <TableRow key={model.id}>
                <TableCell className="font-medium">{model.name}</TableCell>
                <TableCell>{model.provider}</TableCell>
                <TableCell>
                  <Badge variant={model.status === 'active' ? 'success' : model.status === 'error' ? 'error' : 'default'}>
                    {model.status}
                  </Badge>
                </TableCell>
                <TableCell>{model.weight}</TableCell>
                <TableCell>
                  <div className="flex gap-1 flex-wrap">
                    {model.capabilities.map((cap) => (
                      <Badge key={cap} variant="info">{cap}</Badge>
                    ))}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => handleEdit(model)}>编辑</Button>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(model)}>删除</Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} title={editModel?.id ? '编辑模型' : '添加模型'}>
        <div className="space-y-4">
          <Input
            label="模型名称"
            value={editModel?.name || ''}
            onChange={(e) => setEditModel({ ...editModel, name: e.target.value })}
            placeholder="e.g. gpt-4o"
          />
          <Input
            label="Provider"
            value={editModel?.provider || ''}
            onChange={(e) => setEditModel({ ...editModel, provider: e.target.value })}
            placeholder="e.g. OpenAI"
          />
          <Input
            label="权重"
            type="number"
            value={String(editModel?.weight || 100)}
            onChange={(e) => setEditModel({ ...editModel, weight: Number(e.target.value) })}
          />
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={handleSave}>保存</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
