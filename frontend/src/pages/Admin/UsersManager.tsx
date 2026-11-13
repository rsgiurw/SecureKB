/**
 * [MOD EXP-020] 用户管理（管理员功能）
 *
 * 用户列表 + 新增 + 部门调整（部门 = 文档可见权限，与 Security Trimming 同源）
 * + 密码重置 + 删除。API：/api/admin/users（后端 chat_router，admin-only）。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Plus, Trash2, KeyRound, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import api from '../../services/api';
import { useToast } from '@/hooks/use-toast';

interface ManagedUser {
  username: string;
  display_name: string;
  role: 'admin' | 'employee';
  department: string;
  // [MOD EXP-024] 多选部门（列表优先；旧单值数据派生）
  departments?: string[];
}

interface UsersPayload {
  users: ManagedUser[];
  roles: string[];
  departments: string[];
}

export const UsersManager: React.FC = () => {
  const { toast } = useToast();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [roles, setRoles] = useState<string[]>(['admin', 'employee']);
  const [departments, setDepartments] = useState<string[]>(['全员', '技术部', '法务部']);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'employee', department: '全员' });
  const [creating, setCreating] = useState(false);
  const [resetTarget, setResetTarget] = useState<ManagedUser | null>(null);
  const [newPassword, setNewPassword] = useState('');
  // [MOD EXP-024] 多选部门弹窗
  const [deptTarget, setDeptTarget] = useState<ManagedUser | null>(null);
  const [deptSelection, setDeptSelection] = useState<string[]>([]);
  const [me, setMe] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<UsersPayload>('/api/admin/users');
      setUsers(r.data.users);
      setRoles(r.data.roles);
      setDepartments(r.data.departments);
    } catch {
      toast({ variant: 'destructive', title: '加载失败', description: '无法获取用户列表' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
    try {
      setMe(JSON.parse(localStorage.getItem('securekb_user') || 'null')?.username || '');
    } catch { /* ignore */ }
  }, [load]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await api.post('/api/admin/users', form);
      toast({ title: '创建成功', description: `用户 ${form.username} 已添加` });
      setCreateOpen(false);
      setForm({ username: '', password: '', display_name: '', role: 'employee', department: '全员' });
      void load();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail?.message;
      toast({ variant: 'destructive', title: '创建失败', description: msg || '请检查输入' });
    } finally {
      setCreating(false);
    }
  };

  const handleUpdate = async (username: string, patch: Record<string, unknown>) => {
    try {
      await api.put(`/api/admin/users/${username}`, patch);
      toast({ title: '已更新', description: username });
      void load();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail?.message;
      toast({ variant: 'destructive', title: '更新失败', description: msg || '请重试' });
    }
  };

  const handleResetPassword = async () => {
    if (!resetTarget) return;
    await handleUpdate(resetTarget.username, { password: newPassword });
    setResetTarget(null);
    setNewPassword('');
  };

  const handleDelete = async (u: ManagedUser) => {
    if (!window.confirm(`确认删除用户 ${u.username}？其聊天归属标记将一并失效。`)) return;
    try {
      await api.delete(`/api/admin/users/${u.username}`);
      toast({ title: '已删除', description: u.username });
      void load();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: { message?: string } } } }).response?.data?.detail?.message;
      toast({ variant: 'destructive', title: '删除失败', description: msg || '请重试' });
    }
  };

  return (
    <Card className="rounded-2xl border-border/40">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-lg font-black">用户管理</CardTitle>
          <CardDescription className="text-xs">
            新增员工账号；部门决定文档可见范围（与检索权限同源）
          </CardDescription>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="icon" onClick={() => void load()} title="刷新">
            <RefreshCw className="w-4 h-4" />
          </Button>
          <Button size="sm" className="rounded-xl font-bold gap-1.5" onClick={() => setCreateOpen(true)}>
            <Plus className="w-4 h-4" /> 新增用户
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead>显示名</TableHead>
              <TableHead>角色</TableHead>
              <TableHead>部门（文档权限）</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">加载中...</TableCell></TableRow>
            ) : users.map((u) => (
              <TableRow key={u.username}>
                <TableCell className="font-bold">{u.username}</TableCell>
                <TableCell>{u.display_name}</TableCell>
                <TableCell>
                  <Badge variant={u.role === 'admin' ? 'default' : 'secondary'} className="font-bold">
                    {u.role === 'admin' ? '管理员' : '员工'}
                  </Badge>
                </TableCell>
                <TableCell>
                  {/* [MOD EXP-024] 多选部门：chips 显示，点击弹 checkbox 弹窗 */}
                  <button
                    className="flex flex-wrap items-center gap-1 max-w-[180px] text-left"
                    disabled={u.username === me}
                    onClick={() => {
                      setDeptTarget(u);
                      setDeptSelection(u.departments || [u.department]);
                    }}
                    title={u.username === me ? '不能修改自己的部门' : '点击调整文档可见部门（可多选）'}
                  >
                    {(u.departments || [u.department]).map((d) => (
                      <span key={d} className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-bold">
                        {d}
                      </span>
                    ))}
                  </button>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="icon" className="h-7 w-7" title="重置密码"
                      onClick={() => { setResetTarget(u); setNewPassword(''); }}>
                      <KeyRound className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" title="删除"
                      disabled={u.username === me}
                      onClick={() => void handleDelete(u)}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>

      {/* 新增用户 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-lg font-black">新增用户</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-bold">用户名（字母/数字/下划线）</label>
              <Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="例如：zhangsan" />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-bold">初始密码（至少 6 位）</label>
              <Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-bold">显示名</label>
              <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="例如：张三" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-bold">角色</label>
                <select className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm" value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}>
                  {roles.map((r) => <option key={r} value={r}>{r === 'admin' ? '管理员' : '员工'}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs font-bold">部门</label>
                <select className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm" value={form.department}
                  onChange={(e) => setForm({ ...form, department: e.target.value })}>
                  {departments.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button onClick={() => void handleCreate()} disabled={creating || !form.username || !form.password}>
              {creating ? '创建中...' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* [MOD EXP-024] 多选部门（权限分配） */}
      <Dialog open={!!deptTarget} onOpenChange={(isOpen) => { if (!isOpen) setDeptTarget(null); }}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="text-base font-black">调整可见部门 — {deptTarget?.username}</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">勾选该用户可检索的部门（"全员"为所有用户默认可见，勾选即包含）：</p>
          <div className="space-y-2">
            {departments.map((d) => (
              <label key={d} className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  className="accent-primary"
                  checked={deptSelection.includes(d)}
                  onChange={(e) => {
                    // [MOD EXP-024] 清晰语义：至少保留一个部门；
                    // 勾"全员"= 清其他（全员本就含于所有过滤结果）；
                    // 取消"全员"允许（员工至少有一个部门即可）
                    if (e.target.checked) {
                      if (d === '全员') {
                        setDeptSelection(['全员']);
                      } else {
                        setDeptSelection((prev) => [...new Set([...prev.filter((x) => x !== '全员'), d])]);
                      }
                    } else if (deptSelection.length > 1) {
                      setDeptSelection((prev) => prev.filter((x) => x !== d));
                    }
                  }}
                />
                {d}
              </label>
            ))}
          </div>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeptTarget(null)}>取消</Button>
            <Button
              disabled={deptSelection.length === 0}
              onClick={async () => {
                if (!deptTarget) return;
                await handleUpdate(deptTarget.username, { departments: deptSelection });
                setDeptTarget(null);
              }}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重置密码 */}
      <Dialog open={!!resetTarget} onOpenChange={(isOpen) => { if (!isOpen) setResetTarget(null); }}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="text-base font-black">重置密码 — {resetTarget?.username}</DialogTitle>
          </DialogHeader>
          <Input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="新密码（至少 6 位）" />
          <DialogFooter>
            <Button variant="secondary" onClick={() => setResetTarget(null)}>取消</Button>
            <Button onClick={() => void handleResetPassword()} disabled={newPassword.length < 6}>重置</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
};
