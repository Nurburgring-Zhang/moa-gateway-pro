'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { formatDate } from '@/lib/utils';
import type { Workflow } from '@/types';

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState<string | null>(null);

  useEffect(() => {
    loadWorkflows();
  }, []);

  async function loadWorkflows() {
    try {
      const data = await api.getWorkflows();
      setWorkflows(data as unknown as Workflow[]);
    } catch {
      setWorkflows([
        { id: '1', name: '模型健康检查', description: '每5分钟检查所有Provider连通性', status: 'active', last_run: '2024-08-01T10:30:00Z', next_run: '2024-08-01T10:35:00Z', steps: 3 },
        { id: '2', name: '自动扩缩容', description: '根据负载自动调整Provider权重', status: 'active', last_run: '2024-08-01T10:00:00Z', next_run: '2024-08-01T11:00:00Z', steps: 5 },
        { id: '3', name: '日志归档', description: '每日凌晨归档过期日志到对象存储', status: 'active', last_run: '2024-08-01T00:00:00Z', next_run: '2024-08-02T00:00:00Z', steps: 4 },
        { id: '4', name: '配额重置', description: '每月1号重置所有API Key配额', status: 'paused', last_run: '2024-07-01T00:00:00Z', next_run: '2024-09-01T00:00:00Z', steps: 2 },
        { id: '5', name: '模型性能基准测试', description: '每周运行一次性能对比测试', status: 'active', last_run: '2024-07-28T02:00:00Z', next_run: '2024-08-04T02:00:00Z', steps: 7 },
        { id: '6', name: '安全审计扫描', description: '检查异常访问模式和潜在攻击', status: 'error', last_run: '2024-08-01T06:00:00Z', next_run: '2024-08-01T12:00:00Z', steps: 6 },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleTrigger(id: string) {
    setTriggering(id);
    try {
      await api.triggerWorkflow(id);
    } catch {
      // Demo mode
    }
    setTimeout(() => setTriggering(null), 2000);
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">工作流管理</h1>
        <p className="text-sm text-gray-500">{workflows.filter((w) => w.status === 'active').length} 个运行中</p>
      </div>

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>描述</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>步骤数</TableHead>
              <TableHead>上次运行</TableHead>
              <TableHead>下次运行</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflows.map((wf) => (
              <TableRow key={wf.id}>
                <TableCell className="font-medium">{wf.name}</TableCell>
                <TableCell className="text-sm text-gray-500 max-w-xs truncate">{wf.description}</TableCell>
                <TableCell>
                  <Badge variant={
                    wf.status === 'active' ? 'success' :
                    wf.status === 'paused' ? 'warning' : 'error'
                  }>
                    {wf.status}
                  </Badge>
                </TableCell>
                <TableCell>{wf.steps}</TableCell>
                <TableCell className="text-xs text-gray-500">{formatDate(wf.last_run)}</TableCell>
                <TableCell className="text-xs text-gray-500">{formatDate(wf.next_run)}</TableCell>
                <TableCell>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleTrigger(wf.id)}
                    disabled={triggering === wf.id}
                  >
                    {triggering === wf.id ? '执行中...' : '触发'}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
