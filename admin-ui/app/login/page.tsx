'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { setAuth, parseJwt } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await api.login(username, password);
      const payload = parseJwt(res.access_token);
      setAuth(res.access_token, {
        username: (payload?.sub as string) || username,
        // Backend JWTs always carry role; 'unknown' is a defensive fallback
        // (audit fix — never default a missing role to 'admin').
        role: (payload?.role as string) || 'unknown',
      });
      router.push('/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请检查凭据');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-blue-900 to-gray-900 px-4">
      <Card className="w-full max-w-md p-8">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900">
            <span className="text-blue-600">MOA</span> Gateway
          </h1>
          <p className="text-gray-500 mt-2">管理控制台登录</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <Input
            id="username"
            label="用户名"
            type="text"
            placeholder="请输入用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />

          <Input
            id="password"
            label="密码"
            type="password"
            placeholder="请输入密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          {error && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600">
              {error}
            </div>
          )}

          <Button
            type="submit"
            className="w-full"
            size="lg"
            disabled={loading || !username || !password}
          >
            {loading ? '登录中...' : '登 录'}
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-gray-400">
          MOA Gateway Pro Admin Console v3.1.1
        </p>
      </Card>
    </div>
  );
}
