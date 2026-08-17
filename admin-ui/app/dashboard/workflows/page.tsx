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
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);

  useEffect(() => {
    loadWorkflows();
  }, []);

  async function loadWorkflows() {
    setError(null);
    try {
      const data = await api.getWorkflows();
      // Backend returns real DAG templates {name, description, version}.
      setWorkflows((data || []) as unknown as Workflow[]);
    } catch (e) {
      // Honest failure — no fabricated workflow list (audit F6).
      setWorkflows([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleTrigger(name: string) {
    setTriggering(name);
    setError(null);
    setNotice(null);
    try {
      const res = (await api.triggerWorkflow(name)) as {
        result?: { success?: boolean; error?: unknown };
      } | null;
      // Audit fix (P2): only an explicit success:true counts as success.
      // The old `!== false` check reported success even when the backend
      // returned no success field at all.
      if (res?.result?.success === true) {
        setNotice({ ok: true, text: `工作流 "${name}" 已真实执行完成` });
      } else {
        const result = res?.result;
        const detail =
          result && result.error != null
            ? String(result.error)
            : JSON.stringify(res ?? null).slice(0, 200);
        setNotice({
          ok: false,
          text: `工作流 "${name}" 已触发，但执行结果异常：${detail}`,
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTriggering(null);
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">工作流管理</h1>
        <p className="text-sm text-gray-500">{workflows.length} 个工作流模板</p>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}
      {notice && (
        <div
          className={`rounded-md border p-3 text-sm ${
            notice.ok
              ? 'border-green-200 bg-green-50 text-green-700'
              : 'border-yellow-300 bg-yellow-50 text-yellow-800'
          }`}
        >
          {notice.text}
        </div>
      )}

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>描述</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflows.map((wf) => (
              <TableRow key={wf.name}>
                <TableCell className="font-medium">{wf.name}</TableCell>
                <TableCell className="text-sm text-gray-500 max-w-xs truncate">{wf.description}</TableCell>
                <TableCell><Badge variant="info">{String((wf as unknown as { version?: string }).version ?? '-')}</Badge></TableCell>
                <TableCell>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleTrigger(wf.name)}
                    disabled={triggering === wf.name}
                  >
                    {triggering === wf.name ? '执行中...' : '触发'}
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
