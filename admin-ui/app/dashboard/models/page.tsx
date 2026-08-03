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

  useEffect(() => {
    loadModels();
  }, []);

  async function loadModels() {
    try {
      const res = await api.getModels();
      setModels((res.data || []) as unknown as Model[]);
    } catch {
      setModels([
        { id: '1', name: 'gpt-4o', provider: 'OpenAI', status: 'active', weight: 100, capabilities: ['chat', 'vision'], created_at: '2024-01-15' },
        { id: '2', name: 'claude-3.5-sonnet', provider: 'Anthropic', status: 'active', weight: 90, capabilities: ['chat', 'code'], created_at: '2024-02-10' },
        { id: '3', name: 'gemini-pro', provider: 'Google', status: 'inactive', weight: 70, capabilities: ['chat'], created_at: '2024-03-01' },
        { id: '4', name: 'qwen-max', provider: 'Alibaba', status: 'active', weight: 80, capabilities: ['chat', 'code'], created_at: '2024-03-15' },
        { id: '5', name: 'deepseek-v2', provider: 'DeepSeek', status: 'active', weight: 85, capabilities: ['chat', 'code', 'math'], created_at: '2024-04-01' },
      ]);
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
      await loadModels();
    } catch {
      // Demo mode - just close
    }
    setDialogOpen(false);
    setEditModel(null);
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
                    <Button variant="danger" size="sm">删除</Button>
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
