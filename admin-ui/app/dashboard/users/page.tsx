'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog } from '@/components/ui/dialog';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { getRoleColor, formatDate } from '@/lib/utils';
import type { User } from '@/types';

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' });

  useEffect(() => {
    loadUsers();
  }, []);

  async function loadUsers() {
    setError(null);
    try {
      const data = await api.getUsers();
      setUsers((data || []) as unknown as User[]);
    } catch (e) {
      // Honest failure — no fabricated user list (audit F6).
      setUsers([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleRoleChange(userId: string, newRole: string, oldRole: string) {
    setError(null);
    try {
      await api.updateUserRole(userId, newRole);
      setUsers((prev) => prev.map((u) => (String(u.id) === String(userId) ? { ...u, role: newRole as User['role'] } : u)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleCreateUser() {
    if (!newUser.username.trim() || !newUser.password) return;
    setError(null);
    try {
      await api.createUser(newUser.username, newUser.password, newUser.role);
      setDialogOpen(false);
      setNewUser({ username: '', password: '', role: 'user' });
      await loadUsers();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDelete(user: User) {
    if (!window.confirm(`确认删除用户 "${user.username}"?`)) return;
    setError(null);
    try {
      await api.deleteUser(String(user.id));
      await loadUsers();
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
        <h1 className="text-2xl font-bold text-gray-900">用户权限管理</h1>
        <Button onClick={() => setDialogOpen(true)}>+ 添加用户</Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

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
              <TableHead>角色</TableHead>
              <TableHead>最后登录</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((user) => (
              <TableRow key={String(user.id)}>
                <TableCell className="font-medium">{user.username}</TableCell>
                <TableCell>
                  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getRoleColor(user.role)}`}>
                    {user.role}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-gray-500">
                  {user.last_login ? formatDate(user.last_login as unknown as string) : '-'}
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <select
                      value={user.role}
                      onChange={(e) => handleRoleChange(String(user.id), e.target.value, user.role)}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="admin">admin</option>
                      <option value="operator">operator</option>
                      <option value="user">user</option>
                      <option value="readonly">readonly</option>
                    </select>
                    <Button variant="danger" size="sm" onClick={() => handleDelete(user)}>删除</Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} title="添加用户">
        <div className="space-y-4">
          <Input
            label="用户名"
            value={newUser.username}
            onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
            placeholder="e.g. operator1"
          />
          <Input
            label="密码"
            type="password"
            value={newUser.password}
            onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
            placeholder="强密码"
          />
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">角色</label>
            <select
              value={newUser.role}
              onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
              className="w-full text-sm border border-gray-300 rounded-lg px-2 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="admin">admin</option>
              <option value="operator">operator</option>
              <option value="user">user</option>
              <option value="readonly">readonly</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="secondary" onClick={() => setDialogOpen(false)}>取消</Button>
            <Button onClick={handleCreateUser} disabled={!newUser.username.trim() || !newUser.password}>创建</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
