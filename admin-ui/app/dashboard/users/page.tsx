'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { getRoleColor, formatDate } from '@/lib/utils';
import type { User } from '@/types';

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadUsers();
  }, []);

  async function loadUsers() {
    try {
      const data = await api.getUsers();
      setUsers(data as unknown as User[]);
    } catch {
      setUsers([
        { id: '1', username: 'admin', email: 'admin@moa-gateway.io', role: 'admin', status: 'active', last_login: '2024-08-01T10:00:00Z', created_at: '2024-01-01' },
        { id: '2', username: 'operator1', email: 'op1@moa-gateway.io', role: 'operator', status: 'active', last_login: '2024-08-01T08:30:00Z', created_at: '2024-02-15' },
        { id: '3', username: 'developer', email: 'dev@moa-gateway.io', role: 'user', status: 'active', last_login: '2024-07-31T16:00:00Z', created_at: '2024-03-10' },
        { id: '4', username: 'viewer', email: 'view@moa-gateway.io', role: 'readonly', status: 'active', last_login: '2024-07-30T12:00:00Z', created_at: '2024-04-20' },
        { id: '5', username: 'disabled_user', email: 'old@moa-gateway.io', role: 'user', status: 'disabled', last_login: '2024-05-01T00:00:00Z', created_at: '2024-01-15' },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleRoleChange(userId: string, newRole: string) {
    setUsers(users.map((u) => u.id === userId ? { ...u, role: newRole as User['role'] } : u));
    try {
      await api.updateUserRole(userId, newRole);
    } catch {
      // Demo
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">用户权限管理</h1>
        <Button>+ 添加用户</Button>
      </div>

      {/* Role Legend */}
      <div className="flex gap-4 text-sm">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full bg-purple-200" /> Admin - 完全管理权限
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full bg-blue-200" /> Operator - 运维操作权限
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full bg-green-200" /> User - API使用权限
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded-full bg-gray-200" /> Readonly - 只读权限
        </span>
      </div>

      <Card className="p-0 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead>邮箱</TableHead>
              <TableHead>角色</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>最后登录</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-medium">{user.username}</TableCell>
                <TableCell className="text-sm text-gray-500">{user.email}</TableCell>
                <TableCell>
                  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getRoleColor(user.role)}`}>
                    {user.role}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge variant={user.status === 'active' ? 'success' : 'error'}>
                    {user.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-gray-500">{formatDate(user.last_login)}</TableCell>
                <TableCell>
                  <select
                    value={user.role}
                    onChange={(e) => handleRoleChange(user.id, e.target.value)}
                    className="text-sm border border-gray-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="admin">admin</option>
                    <option value="operator">operator</option>
                    <option value="user">user</option>
                    <option value="readonly">readonly</option>
                  </select>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
