import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'MOA Gateway Admin',
  description: 'MOA Gateway Pro 管理控制台',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
