/**
 * [MOD EXP-022] 审计与反馈查看（管理员）
 *
 * GET /api/admin/audit/events：表格 + category 筛选 + 分页加载。
 * category=feedback 即"回答反馈 bad case 列表"（README Roadmap 承诺闭环）。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import api from '../../services/api';
import { formatDate } from '../../i18n/format';
import { useMenuMessages } from '../../i18n/useMenuLocale';

interface AuditEvent {
  id?: string;
  timestamp?: string;
  category?: string;
  action?: string;
  actor_username?: string;
  session_id?: string;
  ip?: string;
  result?: string;
  detail?: Record<string, unknown>;
}

const CATEGORY_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'feedback', label: '回答反馈' },
  { value: 'security', label: '安全事件' },
  { value: 'query', label: '查询' },
  { value: 'admin', label: '管理操作' },
];

const PAGE_SIZE = 50;

export const AuditViewer: React.FC = () => {
  const { locale } = useMenuMessages();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [category, setCategory] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (cat: string) => {
    setLoading(true);
    setError('');
    try {
      const q = cat ? `?category=${encodeURIComponent(cat)}&limit=${PAGE_SIZE}` : `?limit=${PAGE_SIZE}`;
      const r = await api.get(`/api/admin/audit/events${q}`);
      setEvents(r.data.events || []);
    } catch {
      setError('无法加载审计日志（需要管理员权限）');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(category);
  }, [category, load]);

  return (
    <Card className="rounded-2xl border-border/40">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-lg font-black">审计与反馈</CardTitle>
          <CardDescription className="text-xs">
            安全事件 / 用户反馈（👍👎）/ 查询留痕 / 管理操作——append-only
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <Button variant="outline" size="icon" onClick={() => void load(category)} title="刷新">
            <RefreshCw className="w-4 h-4" />
          </Button>
          {/* [MOD EXP-024] 审计导出：当前筛选结果 JSON 下载（展示不全的补齐手段） */}
          <Button variant="outline" size="icon" title="导出当前记录(JSON)"
            disabled={events.length === 0}
            onClick={() => {
              const blob = new Blob([JSON.stringify(events, null, 2)], { type: 'application/json; charset=utf-8' });
              const url = window.URL.createObjectURL(blob);
              const a = globalThis.document.createElement('a');
              a.href = url;
              a.download = `audit_${category || 'all'}_${new Date().toISOString().slice(0, 10)}.json`;
              a.click();
              window.URL.revokeObjectURL(url);
            }}>
            <Download className="w-4 h-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-center text-sm text-muted-foreground py-6">加载中...</p>
        ) : error ? (
          <p className="text-center text-sm text-destructive py-6">{error}</p>
        ) : events.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground py-6">暂无记录</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>类别</TableHead>
                <TableHead>动作</TableHead>
                <TableHead>用户</TableHead>
                <TableHead>详情</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e, idx) => (
                <TableRow key={e.id ?? idx}>
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                    {e.timestamp ? formatDate(e.timestamp, locale) : '-'}
                  </TableCell>
                  <TableCell>
                    <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-muted/40">
                      {e.category}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs font-bold">{e.action}</TableCell>
                  <TableCell className="text-xs">{e.actor_username || '-'}</TableCell>
                  <TableCell className="text-xs text-muted-foreground max-w-[280px] truncate">
                    {e.detail ? JSON.stringify(e.detail).slice(0, 120) : '-'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
};
