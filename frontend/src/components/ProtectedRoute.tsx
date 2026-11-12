import { Navigate } from 'react-router-dom';
import { getStoredUser } from '../services/auth';

interface ProtectedRouteProps {
  children: React.ReactNode;
  title?: string;
}

/**
 * [MOD] SecureKB 管理页路由守卫（改造②语义）。
 *
 * 旧版：要求输入共享访问码（1127，随示例配置公开，毫无保密性）才能进文档管理。
 * 新版：登录身份即授权——管理员（role=admin）直接放行，非管理员回问答页。
 * 服务端另有 get_upload_access 兜底（上传 API 只认 admin 令牌/X-API-Key），
 * 前端这道门只负责导航，不承担真正的权限判定。
 */
export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const user = getStoredUser();

  if (user?.role === 'admin') {
    return <>{children}</>;
  }

  return <Navigate to="/bot" replace />;
}
