'use client';

import { useRouter } from 'next/navigation';
import { clearAuth, getStoredUser } from '@/lib/auth';
import { Button } from '@/components/ui/button';

export function Header() {
  const router = useRouter();
  const user = getStoredUser();

  const handleLogout = () => {
    clearAuth();
    router.push('/login');
  };

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-gray-200 bg-white px-6">
      <div className="flex items-center gap-4">
        <h2 className="text-lg font-semibold text-gray-900">管理控制台</h2>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded-full bg-blue-100 flex items-center justify-center">
            <span className="text-sm font-medium text-blue-600">
              {user?.username?.charAt(0).toUpperCase() || 'A'}
            </span>
          </div>
          <span className="text-sm font-medium text-gray-700">{user?.username || 'Admin'}</span>
        </div>
        <Button variant="ghost" size="sm" onClick={handleLogout}>
          退出
        </Button>
      </div>
    </header>
  );
}
