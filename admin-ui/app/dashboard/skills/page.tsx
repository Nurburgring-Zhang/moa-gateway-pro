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
import type {
  SkillDetailResponse,
  SkillInfo,
  SkillInvokeResponse,
  SkillListResponse,
  SkillSearchResponse,
} from '@/types';

const TIERS = ['free', 'lite', 'standard', 'premium', 'flagship'];

function sourceVariant(source: string): 'success' | 'info' | 'default' {
  switch (source) {
    case 'user':
      return 'success';
    case 'bundled':
      return 'info';
    default:
      return 'default';
  }
}

export default function SkillsPage() {
  const [list, setList] = useState<SkillListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState('');

  // search
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResult, setSearchResult] = useState<SkillSearchResponse | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);

  // detail
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<SkillDetailResponse | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // create
  const [createOpen, setCreateOpen] = useState(false);
  const [createMode, setCreateMode] = useState<'generate' | 'manual'>('generate');
  const [createForm, setCreateForm] = useState({ name: '', description: '', content: '', force_template: false });
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createDone, setCreateDone] = useState<string | null>(null);

  // edit
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SkillInfo | null>(null);
  const [editForm, setEditForm] = useState({ description: '', content: '' });
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // invoke
  const [invokeOpen, setInvokeOpen] = useState(false);
  const [invokeTarget, setInvokeTarget] = useState<SkillInfo | null>(null);
  const [invokeTask, setInvokeTask] = useState('');
  const [invokeTier, setInvokeTier] = useState('standard');
  const [invokeBusy, setInvokeBusy] = useState(false);
  const [invokeError, setInvokeError] = useState<string | null>(null);
  const [invokeResult, setInvokeResult] = useState<SkillInvokeResponse | null>(null);

  useEffect(() => {
    loadSkills('');
  }, []);

  async function loadSkills(source: string) {
    setLoading(true);
    setError(null);
    try {
      setList(await api.getSkills(source || undefined));
      setSourceFilter(source);
      setSearchResult(null);
    } catch (e) {
      setList(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch() {
    if (!searchQuery.trim()) {
      setSearchResult(null);
      return;
    }
    setError(null);
    setSearchBusy(true);
    try {
      setSearchResult(await api.searchSkills(searchQuery.trim(), 10));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearchBusy(false);
    }
  }

  async function openDetail(skill: SkillInfo) {
    setDetailOpen(true);
    setDetail(null);
    setDetailError(null);
    setDetailBusy(true);
    try {
      setDetail(await api.getSkill(skill.name, true));
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailBusy(false);
    }
  }

  async function openEdit(skill: SkillInfo) {
    setEditTarget(skill);
    setEditForm({ description: skill.description ?? '', content: '' });
    setEditError(null);
    setEditOpen(true);
    // Load the full body lazily so the edit form starts from real content.
    try {
      const res = await api.getSkill(skill.name, true);
      setEditForm((prev) => ({
        description: res.skill.description ?? prev.description,
        content: res.skill.content ?? '',
      }));
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleCreate() {
    setCreateError(null);
    setCreateDone(null);
    setCreateBusy(true);
    try {
      let res: Record<string, unknown>;
      if (createMode === 'manual') {
        if (!createForm.name.trim() || !createForm.content) {
          setCreateError('手动模式需要填写名称与 SKILL.md 内容');
          setCreateBusy(false);
          return;
        }
        res = await api.createSkill({
          name: createForm.name.trim(),
          content: createForm.content,
          description: createForm.description.trim() || undefined,
        });
      } else {
        if (!createForm.description.trim()) {
          setCreateError('生成模式需要填写自然语言描述');
          setCreateBusy(false);
          return;
        }
        res = await api.createSkill({
          description: createForm.description.trim(),
          name: createForm.name.trim() || undefined,
          force_template: createForm.force_template,
        });
      }
      const created = res.skill as SkillInfo | undefined;
      setCreateDone(
        `技能 "${created?.name ?? createForm.name}" 创建成功` +
          (typeof res.generated_by === 'string' ? `（${res.generated_by}）` : '')
      );
      setCreateOpen(false);
      setCreateForm({ name: '', description: '', content: '', force_template: false });
      await loadSkills(sourceFilter);
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreateBusy(false);
    }
  }

  async function handleEdit() {
    if (!editTarget) return;
    setEditError(null);
    setEditBusy(true);
    try {
      await api.updateSkill(editTarget.name, {
        description: editForm.description,
        content: editForm.content || undefined,
      });
      setEditOpen(false);
      await loadSkills(sourceFilter);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }

  async function handleDelete(skill: SkillInfo) {
    if (!confirm(`确认删除技能 "${skill.name}"？仅 user 来源的技能可删除。`)) return;
    setError(null);
    try {
      await api.deleteSkill(skill.name);
      await loadSkills(sourceFilter);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function openInvoke(skill: SkillInfo) {
    setInvokeTarget(skill);
    setInvokeTask('');
    setInvokeResult(null);
    setInvokeError(null);
    setInvokeOpen(true);
  }

  async function handleInvoke() {
    if (!invokeTarget || !invokeTask.trim()) return;
    setInvokeError(null);
    setInvokeResult(null);
    setInvokeBusy(true);
    try {
      setInvokeResult(
        await api.invokeSkill(invokeTarget.name, { task: invokeTask.trim(), tier: invokeTier })
      );
    } catch (e) {
      setInvokeError(e instanceof Error ? e.message : String(e));
    } finally {
      setInvokeBusy(false);
    }
  }

  const skills = list?.skills ?? [];

  function renderActions(skill: SkillInfo) {
    const isUser = skill.source === 'user';
    return (
      <div className="flex items-center gap-1.5">
        <Button variant="secondary" size="sm" onClick={() => openDetail(skill)}>详情</Button>
        <Button variant="secondary" size="sm" onClick={() => openInvoke(skill)}>演练</Button>
        {isUser && (
          <>
            <Button variant="secondary" size="sm" onClick={() => openEdit(skill)}>编辑</Button>
            <Button variant="danger" size="sm" onClick={() => handleDelete(skill)}>删除</Button>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">技能中心（v4.1）</h1>
        <div className="flex items-center gap-2">
          {list && (
            <Badge variant="info">
              bundled {list.sources.bundled ?? 0} / extra {list.sources.extra ?? 0} / user {list.sources.user ?? 0}
            </Badge>
          )}
          <Button onClick={() => { setCreateDone(null); setCreateError(null); setCreateOpen(true); }}>+ 新建技能</Button>
        </div>
      </div>

      {createDone && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">{createDone}</div>
      )}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Toolbar: search + source filter */}
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 pt-1">
          <div className="flex-1 min-w-64">
            <Input
              label="模糊搜索（名称 / 描述 / 触发词）"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="输入关键词后回车或点击搜索"
            />
          </div>
          <Button onClick={handleSearch} disabled={searchBusy || !searchQuery.trim()}>
            {searchBusy ? '搜索中…' : '搜索'}
          </Button>
          {searchResult && (
            <Button variant="secondary" onClick={() => { setSearchResult(null); setSearchQuery(''); }}>
              返回完整列表
            </Button>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">来源</label>
            <select
              value={sourceFilter}
              onChange={(e) => loadSkills(e.target.value)}
              className="text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">全部</option>
              <option value="bundled">bundled</option>
              <option value="extra">extra</option>
              <option value="user">user</option>
            </select>
          </div>
        </CardContent>
      </Card>

      {/* Search results */}
      {searchResult ? (
        <Card className="p-0 overflow-hidden">
          <CardHeader className="px-6 pt-5">
            <CardTitle>搜索结果（{searchResult.count} 条，查询：{searchResult.query}）</CardTitle>
          </CardHeader>
          {searchResult.results.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>得分</TableHead>
                  <TableHead>技能</TableHead>
                  <TableHead>描述</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>得分构成</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {searchResult.results.map((r) => (
                  <TableRow key={r.skill.name}>
                    <TableCell className="font-mono">{r.score.toFixed(3)}</TableCell>
                    <TableCell className="font-medium">{r.skill.name}</TableCell>
                    <TableCell className="max-w-md whitespace-normal text-gray-600">{r.skill.description}</TableCell>
                    <TableCell><Badge variant={sourceVariant(r.skill.source)}>{r.skill.source}</Badge></TableCell>
                    <TableCell className="text-xs text-gray-500 whitespace-normal">
                      {Object.entries(r.breakdown).map(([k, v]) => `${k}=${v.toFixed(2)}`).join(' ')}
                    </TableCell>
                    <TableCell>{renderActions(r.skill)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <CardContent className="text-sm text-gray-500">没有匹配的技能。</CardContent>
          )}
        </Card>
      ) : (
        <Card className="p-0 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-40">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
            </div>
          ) : skills.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>技能</TableHead>
                  <TableHead>描述</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>触发词</TableHead>
                  <TableHead>内容长度</TableHead>
                  <TableHead>优先级</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {skills.map((skill) => (
                  <TableRow key={skill.name}>
                    <TableCell className="font-medium">
                      {skill.name}
                      {skill.name_zh && <span className="ml-1 text-xs text-gray-400">{skill.name_zh}</span>}
                    </TableCell>
                    <TableCell className="max-w-md whitespace-normal text-gray-600">{skill.description}</TableCell>
                    <TableCell><Badge variant={sourceVariant(skill.source)}>{skill.source}</Badge></TableCell>
                    <TableCell className="text-xs text-gray-500 whitespace-normal max-w-48">
                      {skill.triggers.length > 0 ? skill.triggers.slice(0, 5).join('、') : '-'}
                      {skill.triggers.length > 5 ? `（+${skill.triggers.length - 5}）` : ''}
                    </TableCell>
                    <TableCell>{skill.content_chars}</TableCell>
                    <TableCell>{skill.priority}</TableCell>
                    <TableCell>{renderActions(skill)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <CardContent className="text-sm text-gray-500">
              暂无技能——可能技能目录为空，或 skillhub 模块未启用。
            </CardContent>
          )}
        </Card>
      )}

      {/* Detail dialog */}
      <Dialog open={detailOpen} onClose={() => setDetailOpen(false)} title={`技能详情：${detail?.skill.name ?? '…'}`} className="max-w-2xl">
        {detailBusy && (
          <div className="flex items-center justify-center h-24">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600" />
          </div>
        )}
        {detailError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{detailError}</div>
        )}
        {detail && !detailBusy && (
          <div className="space-y-3 max-h-[70vh] overflow-auto">
            <div className="flex flex-wrap gap-2">
              <Badge variant={sourceVariant(detail.skill.source)}>{detail.skill.source}</Badge>
              <Badge variant="default">优先级 {detail.skill.priority}</Badge>
              {detail.skill.user_invocable && <Badge variant="success">用户可调用</Badge>}
              {detail.skill.fork_agent && <Badge variant="info">fork_agent</Badge>}
              {detail.skill.model && <Badge variant="default">model: {detail.skill.model}</Badge>}
            </div>
            <p className="text-sm text-gray-700">{detail.skill.description}</p>
            {detail.skill.triggers.length > 0 && (
              <p className="text-xs text-gray-500">触发词：{detail.skill.triggers.join('、')}</p>
            )}
            <p className="text-xs text-gray-500 break-all">目录：{detail.skill.dir_path}</p>
            {detail.usage && Object.keys(detail.usage).length > 0 && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 mb-1">使用统计</h4>
                <pre className="rounded-md bg-gray-50 border border-gray-200 p-3 text-xs whitespace-pre-wrap break-all">
                  {JSON.stringify(detail.usage, null, 2)}
                </pre>
              </div>
            )}
            {detail.skill.content && (
              <div>
                <h4 className="text-sm font-medium text-gray-700 mb-1">SKILL.md 内容</h4>
                <pre className="rounded-md bg-gray-50 border border-gray-200 p-3 text-xs whitespace-pre-wrap break-all max-h-64 overflow-auto">
                  {detail.skill.content}
                </pre>
              </div>
            )}
          </div>
        )}
      </Dialog>

      {/* Create dialog */}
      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} title="新建技能" className="max-w-2xl">
        <div className="space-y-4">
          <div className="flex gap-2">
            <Button variant={createMode === 'generate' ? 'primary' : 'secondary'} size="sm" onClick={() => setCreateMode('generate')}>
              自然语言生成
            </Button>
            <Button variant={createMode === 'manual' ? 'primary' : 'secondary'} size="sm" onClick={() => setCreateMode('manual')}>
              手动编写
            </Button>
          </div>
          {createError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{createError}</div>
          )}
          {createMode === 'generate' ? (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">自然语言描述 *</label>
                <textarea
                  value={createForm.description}
                  onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                  rows={4}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="描述这个技能要做什么，后端会生成合法的 SKILL.md（有 LLM 走 LLM，否则走确定性模板）"
                />
              </div>
              <Input
                label="名称（可选，留空由后端生成）"
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
              />
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={createForm.force_template}
                  onChange={(e) => setCreateForm({ ...createForm, force_template: e.target.checked })}
                />
                强制使用确定性模板引擎（跳过 LLM 生成路径）
              </label>
            </>
          ) : (
            <>
              <Input
                label="技能名称 *"
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                placeholder="my-skill"
              />
              <Input
                label="描述（可选，写入 frontmatter description）"
                value={createForm.description}
                onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
              />
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">SKILL.md 内容 *</label>
                <textarea
                  value={createForm.content}
                  onChange={(e) => setCreateForm({ ...createForm, content: e.target.value })}
                  rows={10}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder={'---\nname: my-skill\ndescription: ...\n---\n\n技能正文…'}
                />
              </div>
            </>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button onClick={handleCreate} disabled={createBusy}>{createBusy ? '创建中…' : '创建'}</Button>
          </div>
        </div>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={editOpen} onClose={() => setEditOpen(false)} title={`编辑技能：${editTarget?.name ?? ''}`} className="max-w-2xl">
        <div className="space-y-4">
          {editError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{editError}</div>
          )}
          <Input
            label="描述"
            value={editForm.description}
            onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
          />
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">SKILL.md 内容（留空则保留原内容）</label>
            <textarea
              value={editForm.content}
              onChange={(e) => setEditForm({ ...editForm, content: e.target.value })}
              rows={12}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <Button variant="secondary" onClick={() => setEditOpen(false)}>取消</Button>
            <Button onClick={handleEdit} disabled={editBusy}>{editBusy ? '保存中…' : '保存'}</Button>
          </div>
        </div>
      </Dialog>

      {/* Invoke dialog */}
      <Dialog open={invokeOpen} onClose={() => setInvokeOpen(false)} title={`Invoke 演练：${invokeTarget?.name ?? ''}`} className="max-w-2xl">
        <div className="space-y-4">
          {invokeError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{invokeError}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">任务描述 *</label>
            <textarea
              value={invokeTask}
              onChange={(e) => setInvokeTask(e.target.value)}
              rows={4}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="在该技能上下文下执行的任务"
            />
          </div>
          <div className="w-48">
            <label className="block text-sm font-medium text-gray-700 mb-1">模型档位</label>
            <select
              value={invokeTier}
              onChange={(e) => setInvokeTier(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {TIERS.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-3">
            <Button variant="secondary" onClick={() => setInvokeOpen(false)}>关闭</Button>
            <Button onClick={handleInvoke} disabled={invokeBusy || !invokeTask.trim()}>
              {invokeBusy ? '执行中…' : '执行'}
            </Button>
          </div>
          {invokeResult && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-2">
              <div className="flex flex-wrap gap-2">
                {invokeResult.model && <Badge variant="info">model: {invokeResult.model}</Badge>}
                {invokeResult.provider && <Badge variant="default">provider: {invokeResult.provider}</Badge>}
                {invokeResult.endpoint_id && <Badge variant="default">endpoint: {invokeResult.endpoint_id}</Badge>}
                {invokeResult.finish_reason && <Badge variant="default">finish: {invokeResult.finish_reason}</Badge>}
                <Badge variant="success">
                  tokens {invokeResult.prompt_tokens ?? 0}+{invokeResult.completion_tokens ?? 0}={invokeResult.total_tokens ?? 0}
                </Badge>
                {invokeResult.latency_ms != null && <Badge variant="default">{invokeResult.latency_ms} ms</Badge>}
              </div>
              {invokeResult.content && (
                <pre className="max-h-64 overflow-auto rounded-md bg-white border border-blue-100 p-3 text-xs whitespace-pre-wrap break-all">
                  {invokeResult.content}
                </pre>
              )}
              {invokeResult.evolution && (
                <div className="text-xs text-gray-600">
                  <span className="font-medium">演化里程碑：</span>
                  {JSON.stringify(invokeResult.evolution)}
                </div>
              )}
            </div>
          )}
        </div>
      </Dialog>
    </div>
  );
}
