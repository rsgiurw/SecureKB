import React, { useState, useEffect, useRef } from 'react';
import { Sidebar } from './Sidebar';
import { PageHeader } from './PageHeader';
import { useLocation, useNavigate } from 'react-router-dom';
import { healthAPI } from '../services/api';
import { removeAdminAccess } from '../utils/accessControl';
import { Toaster } from '@/components/ui/toaster';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { cn } from '@/lib/utils';
import { useMenuMessages } from '../i18n/useMenuLocale';

interface AppLayoutProps {
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({ children }) => {
  const { messages } = useMenuMessages();
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => {
    const saved = localStorage.getItem('sidebarOpen');
    return saved ? Boolean(JSON.parse(saved)) : true;
  });
  const [darkMode, setDarkMode] = useState(() => {
    const saved = localStorage.getItem('darkMode');
    return saved ? JSON.parse(saved) : false;
  });
  const [serverStatus, setServerStatus] = useState<'healthy' | 'unhealthy' | 'checking'>('checking');
  const location = useLocation();
  const navigate = useNavigate();

  // 반응형: 화면이 좁아지면 메인 사이드바 자동 닫기
  const isSmallScreen = useMediaQuery('(max-width: 768px)');
  // 직전 브레이크포인트 추적 — '데스크톱→모바일 전환' 또는 '모바일 최초 진입'에만
  // 자동으로 닫고, 모바일에서 사용자가 직접 연 사이드바는 닫지 않기 위함.
  const previousIsSmallScreenRef = useRef<boolean | null>(null);

  // 서버 상태 확인
  useEffect(() => {
    const checkHealth = async () => {
      try {
        const response = await healthAPI.check();
        setServerStatus(response.data.status === 'OK' ? 'healthy' : 'unhealthy');
      } catch {
        setServerStatus('unhealthy');
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 30000);

    return () => clearInterval(interval);
  }, []);

  // 사이드바 상태 저장
  useEffect(() => {
    localStorage.setItem('sidebarOpen', JSON.stringify(sidebarOpen));
  }, [sidebarOpen]);

  // 반응형: 화면이 좁아지는 '전환 시점'(또는 모바일 최초 진입)에만 사이드바 자동 닫기.
  // sidebarOpen을 의존성에 두면 모바일에서 열 때마다 effect가 재발동해 즉시 닫히므로,
  // 직전 브레이크포인트(ref)를 비교해 전환 시점만 처리하고 deps는 [isSmallScreen]로 한정.
  useEffect(() => {
    const previousIsSmallScreen = previousIsSmallScreenRef.current;
    if (isSmallScreen && previousIsSmallScreen !== true) {
      setSidebarOpen(false);
    }
    previousIsSmallScreenRef.current = isSmallScreen;
  }, [isSmallScreen]);

  // 다크모드 토글
  const toggleDarkMode = () => {
    const newMode = !darkMode;
    setDarkMode(newMode);
    localStorage.setItem('darkMode', JSON.stringify(newMode));
    if (newMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  };

  // 초기 다크모드 적용
  useEffect(() => {
    const savedMode = localStorage.getItem('darkMode');
    const isDark = savedMode ? JSON.parse(savedMode) : false;
    setDarkMode(isDark);
    if (isDark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, []);

  const handleLogout = () => {
    // [MOD] 改造②：完整登出——清除登录令牌与用户信息，回到登录门
    removeAdminAccess();
    try {
      localStorage.removeItem('securekb_token');
      localStorage.removeItem('securekb_user');
    } catch {
      /* ignore */
    }
    window.location.href = '/bot';
  };

  const handleMenuClick = (path: string) => {
    navigate(path);
  };

  // 현재 페이지 이름 추출
  const getCurrentPageName = () => {
    const path = location.pathname;
    if (path === '/bot') return messages.nav.chatbot;
    if (path === '/upload') return messages.nav.documents;
    if (path === '/prompts') return messages.nav.prompts;
    if (path === '/admin') return messages.nav.adminDashboard;
    if (path === '/admin/settings') return messages.nav.globalOperationSettings;
    return '';
  };

  const pageName = getCurrentPageName();

  return (
    <div className="flex min-h-screen bg-background text-foreground transition-colors duration-300">
      <Sidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((open) => !open)}
      />

      <main
        className={cn(
          "flex-grow flex flex-col min-w-0 transition-all duration-300 ease-in-out"
        )}
        // [FIX 设计评审 P0] Sidebar 是 relative shrink-0（文档流内已占位 240px），
        // 此前的 inline marginLeft 造成双重偏移 → 1440px 下出现 243px 死白列、
        // 聊天区被挤到 ~312px。删除偏移后由 Sidebar 单独占位。
      >
        {pageName && (
          <PageHeader
            pageName={pageName}
            darkMode={darkMode}
            serverStatus={serverStatus}
            onToggleDarkMode={toggleDarkMode}
            onLogout={handleLogout}
            onMenuClick={handleMenuClick}
            showNavigation={false}
          />
        )}

        <div className="flex-grow flex flex-col p-4 md:p-6 bg-muted/20">
          {children}
        </div>
      </main>

      <Toaster />
    </div>
  );
};

export default AppLayout;

