'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Toggle } from '@/components/ui/toggle';
import type { Capability } from '@/types';

const CAPABILITIES: Capability[] = [
  { name: 'chat', display_name: '对话补全', enabled: true, provider: 'OpenAI/Anthropic', description: '标准对话API', config: {} },
  { name: 'vision', display_name: '视觉理解', enabled: true, provider: 'OpenAI/Google', description: '图片分析与理解', config: {} },
  { name: 'image_gen', display_name: '图像生成', enabled: true, provider: 'OpenAI/Stability', description: 'DALL-E / Stable Diffusion', config: {} },
  { name: 'tts', display_name: '语音合成', enabled: true, provider: 'OpenAI', description: '文本转语音', config: {} },
  { name: 'stt', display_name: '语音识别', enabled: false, provider: 'OpenAI/Whisper', description: '语音转文本', config: {} },
  { name: 'embedding', display_name: '文本嵌入', enabled: true, provider: 'OpenAI/Cohere', description: '向量化', config: {} },
  { name: 'code', display_name: '代码生成', enabled: true, provider: 'OpenAI/DeepSeek', description: '代码补全与生成', config: {} },
  { name: 'reasoning', display_name: '推理', enabled: true, provider: 'OpenAI/DeepSeek', description: '复杂推理任务', config: {} },
  { name: 'search', display_name: '联网搜索', enabled: false, provider: 'Perplexity', description: '实时网络搜索', config: {} },
  { name: 'video', display_name: '视频理解', enabled: false, provider: 'Google/Gemini', description: '视频内容分析', config: {} },
  { name: 'function_call', display_name: '函数调用', enabled: true, provider: 'OpenAI/Anthropic', description: 'Tool Use / Function Calling', config: {} },
];

export default function CapabilityPage() {
  // Static display metadata (names/descriptions are UI copy). The `enabled`
  // state is ALWAYS sourced from the backend — never fabricated (audit F6).
  const [capabilities, setCapabilities] = useState<Capability[]>(CAPABILITIES);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadCapabilities();
  }, []);

  async function loadCapabilities() {
    setError(null);
    try {
      const data = await api.getCapabilities();
      if (Array.isArray(data)) {
        const byName = new Map(data.map((c) => [String(c.name), c]));
        // Overlay real enabled-state onto the display metadata.
        const merged = CAPABILITIES.map((c) => {
          const live = byName.get(c.name);
          return live ? { ...c, enabled: Boolean(live.enabled) } : { ...c, enabled: false };
        });
        // Append backend capabilities not in the static display list.
        const known = new Set(CAPABILITIES.map((c) => c.name));
        for (const c of data) {
          const name = String(c.name);
          if (!known.has(name)) {
            merged.push({
              name,
              display_name: name,
              enabled: Boolean(c.enabled),
              provider: '',
              description: '',
              config: {},
            });
          }
        }
        setCapabilities(merged);
      }
    } catch (e) {
      // Audit fix: on failure do NOT show the hardcoded initial toggles —
      // clear to an empty list so no fabricated enabled-state is presented.
      setCapabilities([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleToggle(name: string) {
    const cap = capabilities.find((c) => c.name === name);
    if (!cap) return;
    const next = !cap.enabled;
    // Optimistic update, reverted on real failure.
    setCapabilities((prev) => prev.map((c) => (c.name === name ? { ...c, enabled: next } : c)));
    try {
      await api.updateCapability(name, { enabled: next });
    } catch (e) {
      setCapabilities((prev) => prev.map((c) => (c.name === name ? { ...c, enabled: !next } : c)));
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">多模态能力管理</h1>
        <Badge variant="info">{capabilities.filter((c) => c.enabled).length} / {capabilities.length} 已启用</Badge>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {capabilities.map((cap) => (
          <Card key={cap.name} className={!cap.enabled ? 'opacity-60' : ''}>
            <CardHeader className="flex flex-row items-center justify-between mb-2">
              <CardTitle className="text-base">{cap.display_name}</CardTitle>
              <Toggle checked={cap.enabled} onChange={() => handleToggle(cap.name)} />
            </CardHeader>
            <CardContent>
              <p className="text-sm text-gray-500 mb-3">{cap.description}</p>
              <div className="flex items-center justify-between">
                <Badge variant={cap.enabled ? 'success' : 'default'}>
                  {cap.enabled ? '已启用' : '已禁用'}
                </Badge>
                <span className="text-xs text-gray-400">{cap.provider}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
