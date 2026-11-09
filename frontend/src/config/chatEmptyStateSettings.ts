/**
 * ChatEmptyState 설정 상수
 *
 * 챗봇 Empty State의 기본 설정값을 정의합니다.
 */

import type { ChatEmptyStateSettings } from '../types';

/**
 * ChatEmptyState 기본 설정
 */
export const CHAT_EMPTY_STATE_SETTINGS: ChatEmptyStateSettings = {
  mainMessage: '有什么可以帮您吗？',
  subMessage: 'AI 分析参考文档以提供准确的回答',
  suggestions: [
    '请总结这份文档的重点',
    '默认问题 1',
    '默认问题 2',
    '默认问题 3',
  ],
};

