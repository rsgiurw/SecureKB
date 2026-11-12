// [MOD] 登录门：认证态存取（从 LoginGate.tsx 拆出——组件文件仅导出组件，
// 否则违反 react-refresh/only-export-components 规则、破坏 Fast Refresh）
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
