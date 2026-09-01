'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { formatDate } from '@/lib/utils';
import type { ChannelListResponse, ChannelStatus } from '@/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8910';

function stateVariant(ch: ChannelStatus): 'success' | 'info' | 'warning' | 'default' {
  if (ch.running) return 'success';
  if (ch.configured) return 'info';
  return 'warning';
}

export default function ChannelsPage() {
  const [data, setData] = useState<ChannelListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [sendForm, setSendForm] = useState({ platform: '', chat_id: '', text: '' });
  const [sendBusy, setSendBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    loadChannels();
  }, []);

  async function loadChannels() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getChannels();
      setData(res);
      // Default the send form to the first configured platform (real state).
      const firstConfigured = (res.channels ?? []).find((c) => c.configured);
      setSendForm((prev) => ({ ...prev, platform: prev.platform || firstConfigured?.platform || '' }));
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleSend() {
    setSendError(null);
    setSendResult(null);
    if (!sendForm.platform || !sendForm.chat_id.trim() || !sendForm.text.trim()) {
      setSendError('平台、chat_id 与消息文本均为必填');
      return;
    }
    setSendBusy(true);
    try {
      setSendResult(
        await api.sendChannelMessage(sendForm.platform, {
          chat_id: sendForm.chat_id.trim(),
          text: sendForm.text,
        })
      );
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSendBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const channels = data?.channels ?? [];
  const configuredPlatforms = channels.filter((c) => c.configured);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">IM 渠道（v4.1）</h1>
        <div className="flex items-center gap-2">
          {data && (
            <>
              <Badge variant="info">已配置 {data.configured} / {data.count}</Badge>
              {data.enabled.length > 0 && (
                <Badge variant="success">{data.enabled.join('、')}</Badge>
              )}
            </>
          )}
          <Button variant="secondary" onClick={loadChannels}>刷新</Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Adapter status cards */}
      {channels.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {channels.map((ch) => (
            <Card key={ch.platform} className={!ch.configured ? 'opacity-80' : ''}>
              <CardHeader className="flex flex-row items-center justify-between mb-2">
                <CardTitle className="text-base capitalize">{ch.platform}</CardTitle>
                <Badge variant={stateVariant(ch)}>{ch.state}</Badge>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="text-xs text-gray-500">
                  最近活动：{ch.last_activity == null ? '无' : formatDate(ch.last_activity)}
                </div>
                <div>
                  <div className="text-xs font-medium text-gray-700 mb-1">所需环境变量</div>
                  <div className="flex flex-wrap gap-1">
                    {ch.required_env.map((env) => {
                      const missing = ch.missing_env.some((m) => m.includes(env));
                      return (
                        <code
                          key={env}
                          className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                            missing ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-700'
                          }`}
                        >
                          {env}
                        </code>
                      );
                    })}
                  </div>
                </div>
                {ch.missing_env.length > 0 && (
                  <div className="text-xs text-red-600">
                    未配置：{ch.missing_env.join('；')}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="text-sm text-gray-500">
            后端未返回任何渠道适配器（channels 模块可能未启用）。
          </CardContent>
        </Card>
      )}

      {/* Configuration guide */}
      <Card>
        <CardHeader>
          <CardTitle>接入配置指引</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            在网关进程的环境中配置对应平台的 MOA_* 变量后，适配器即在下次请求时进入 configured 状态；
            入站回调统一指向各平台的 Webhook URL（无需鉴权头，平台签名即认证）。
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {channels.map((ch) => (
            <div key={ch.platform} className="rounded-lg border border-gray-200 p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-medium capitalize">{ch.platform}</span>
                <Badge variant={stateVariant(ch)}>{ch.configured ? '已配置' : '未配置'}</Badge>
              </div>
              <div className="text-xs text-gray-600 space-y-1">
                <div>
                  <span className="font-medium">环境变量：</span>
                  {ch.required_env.join('、') || '（无）'}
                </div>
                <div className="break-all">
                  <span className="font-medium">Webhook URL：</span>
                  <code className="font-mono">{API_BASE}/v1/channels/{ch.platform}/webhook</code>
                </div>
                <div className="text-gray-400">
                  在各平台开放后台创建机器人 / 应用，填入上述凭证，并把事件回调地址指向该 Webhook URL。
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Send test */}
      <Card>
        <CardHeader>
          <CardTitle>发送测试（POST /v1/channels/&#123;name&#125;/send）</CardTitle>
          <p className="text-sm text-gray-500 mt-1">
            通过所选平台的真实 API 发送一条消息（需要管理员权限；未配置的平台会返回 409 与缺失变量清单）。
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">平台</label>
              <select
                value={sendForm.platform}
                onChange={(e) => setSendForm({ ...sendForm, platform: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">（选择平台）</option>
                {channels.map((c) => (
                  <option key={c.platform} value={c.platform}>
                    {c.platform}{c.configured ? '' : '（未配置）'}
                  </option>
                ))}
              </select>
              {configuredPlatforms.length === 0 && channels.length > 0 && (
                <p className="mt-1 text-xs text-gray-400">当前没有已配置的平台</p>
              )}
            </div>
            <Input
              label="chat_id"
              value={sendForm.chat_id}
              onChange={(e) => setSendForm({ ...sendForm, chat_id: e.target.value })}
              placeholder="目标会话 ID"
            />
            <div className="flex items-end">
              <Button onClick={handleSend} disabled={sendBusy} className="w-full">
                {sendBusy ? '发送中…' : '发送测试消息'}
              </Button>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">消息文本</label>
            <textarea
              value={sendForm.text}
              onChange={(e) => setSendForm({ ...sendForm, text: e.target.value })}
              rows={3}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="要发送的内容"
            />
          </div>
          {sendError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{sendError}</div>
          )}
          {sendResult && (
            <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
              <div className="font-medium mb-1">平台返回：</div>
              <pre className="whitespace-pre-wrap break-all font-mono text-xs">{JSON.stringify(sendResult, null, 2)}</pre>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
