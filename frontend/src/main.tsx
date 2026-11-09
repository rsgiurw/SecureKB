import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'
import { logger } from './utils/logger'

// 디버깅용 로그
logger.log('🚀 React 앱 시작...');

try {
  const rootElement = document.getElementById('root');
  if (!rootElement) {
    throw new Error('Root element not found');
  }

  logger.log('✅ Root element found:', rootElement);

  const root = createRoot(rootElement);

  root.render(
    <StrictMode>
      <App />
    </StrictMode>
  );

  logger.log('✅ React 앱 렌더링 完成');
} catch (error) {
  logger.error('❌ React 앱 렌더링 失败:', error);

  // 간단한 fallback HTML 표시
  const rootElement = document.getElementById('root');
  if (rootElement) {
    rootElement.innerHTML = `
      <div style="padding: 20px; font-family: Arial, sans-serif;">
        <h1 style="color: red;">React 앱 로드 失败</h1>
        <p>오류: ${error}</p>
        <p>F12를 눌러 개발자도구 콘솔을 확인하세요.</p>
        <button onclick="window.location.reload()">페이지 새로고침</button>
      </div>
    `;
  }
}
