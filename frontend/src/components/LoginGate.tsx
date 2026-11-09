// [MOD] 登录门：SecureKB 仅中文登录界面（用户名/密码 → 后端签发令牌）
import { useState } from 'react';
import api from '../services/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';

export interface AuthUser {
  username: string;
  display_name: string;
  role: 'admin' | 'employee';
  department: string;
}

export const TOKEN_STORAGE_KEY = 'securekb_token';
export const USER_STORAGE_KEY = 'securekb_user';

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(USER_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

interface LoginGateProps {
  onSuccess: (user: AuthUser) => void;
}

export function LoginGate({ onSuccess }: LoginGateProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleLogin() {
    if (!username.trim() || !password) {
      setError('请输入用户名和密码');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const r = await api.post('/api/auth/login', { username, password });
      const { token, user } = r.data;
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
      onSuccess(user as AuthUser);
    } catch (e) {
      const status = (e as { response?: { status?: number } }).response?.status;
      setError(status === 401 ? '用户名或密码错误' : '登录失败，请确认后端服务正在运行');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold tracking-tight">SecureKB</CardTitle>
          <CardDescription>企业知识库问答 · 请登录后使用</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="login-username" className="text-sm font-medium">用户名</label>
            <input
              id="login-username"
              className="w-full h-9 rounded-lg border border-input bg-transparent px-3 text-sm"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="例如：admin / tech / legal / staff"
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="login-password" className="text-sm font-medium">密码</label>
            <input
              id="login-password"
              type="password"
              className="w-full h-9 rounded-lg border border-input bg-transparent px-3 text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button className="w-full" onClick={handleLogin} disabled={loading}>
            {loading ? '登录中...' : '登录'}
          </Button>
          <p className="text-xs text-muted-foreground text-center">
            管理员可见全部文档；普通员工仅可检索"全员"及本部门文档
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
