import { cn, formatNumber } from '@/lib/utils';
import { Card } from '@/components/ui/card';

interface StatsCardProps {
  title: string;
  value: number | string;
  icon: string;
  trend?: number;
  color?: 'blue' | 'green' | 'purple' | 'orange';
}

export function StatsCard({ title, value, icon, trend, color = 'blue' }: StatsCardProps) {
  const colors = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
  };

  const displayValue = typeof value === 'number' ? formatNumber(value) : value;

  return (
    <Card className="flex items-center gap-4">
      <div className={cn('rounded-lg p-3', colors[color])}>
        <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
        </svg>
      </div>
      <div>
        <p className="text-sm text-gray-500">{title}</p>
        <p className="text-2xl font-bold text-gray-900">{displayValue}</p>
        {trend !== undefined && (
          <p className={cn('text-xs mt-0.5', trend >= 0 ? 'text-green-600' : 'text-red-600')}>
            {trend >= 0 ? '↑' : '↓'} {Math.abs(trend)}% vs 昨日
          </p>
        )}
      </div>
    </Card>
  );
}
