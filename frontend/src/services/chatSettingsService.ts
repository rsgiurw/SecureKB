/**
 * ChatEmptyState 설정 관리 서비스 (서버 기반, 로케일별)
 *
 * 챗봇 Empty State의 메인/보조 메시지와 추천 질문을 **서버**에서 로케일별(ko/en)로
 * 관리합니다. 관리자가 저장하면 모든 사용자에게 반영됩니다(다중 사용자 결함 수정).
 *
 * 백엔드 계약(OneRAG empty_state_router, prefix=/api):
 * - 공개 조회: GET    /api/chat-empty-state               → { [locale]: { mainMessage, subMessage, suggestions } }
 * - 관리자 저장: PUT   /api/admin/chat-empty-state/{locale} (X-API-Key) → { mainMessage, subMessage, suggestions }
 * - 관리자 리셋: DELETE /api/admin/chat-empty-state/{locale} (X-API-Key) → 기본값 { ... }
 *
 * localStorage는 더 이상 원본(source of truth)이 아니라 **오프라인/즉시 렌더용 캐시**로만
 * 사용합니다. 서버 미응답 시 캐시→기본값으로 graceful 폴백합니다.
 * 관리자 X-API-Key는 운영 설정(operatorSettings.adminApiKey)에서만 읽습니다.
 */

import { ChatEmptyStateSettings, ChatEmptyStateSettingsUpdateRequest } from '../types';
import { MENU_LOCALES, type MenuLocale } from '../i18n/menuMessages';
import { getAdminAPIBaseURL } from './adminService';
import { logger } from '../utils/logger';
import {
  getRequiredAdminAuthHeaders,
  hasAdminApiKey,
} from './authHeaders';

export { MissingAdminKeyError } from './authHeaders';

// 전 로케일 설정 묶음 (ko/en).
export type AllEmptyStateSettings = Record<MenuLocale, ChatEmptyStateSettings>;

// 로케일별 기본값 — 오프라인/첫 렌더 폴백.
// OneRAG 범용 韩语 기본을 유지하며, 일본어 등 도메인/언어 특화 문자열은 차용하지 않는다.
// (백엔드 _empty_state_defaults.py의 코드 기본값과 의미적으로 일치)
const DEFAULTS: AllEmptyStateSettings = {
  ko: {
    mainMessage: '有什么可以帮您吗？',
    subMessage: 'AI 分析参考文档以提供准确的回答',
    suggestions: [
      '总结公司信息安全制度的核心要求',
      '默认问题 1',
      '默认问题 2',
      '默认问题 3',
    ],
  },
  en: {
    mainMessage: 'How can I help you?',
    subMessage: 'AI analyzes reference documents to provide accurate answers',
    suggestions: [
      'Summarize the key points of this document',
      'Sample question 1',
      'Sample question 2',
      'Sample question 3',
    ],
  },
  ja: {
    mainMessage: '何かお手伝いできますか？',
    subMessage: 'AIが参考文書を分析して正確な回答を提供します',
    suggestions: [
      'この文書の要点を要約してください',
      'サンプル質問1',
      'サンプル質問2',
      'サンプル質問3',
    ],
  },
  es: {
    mainMessage: '¿En qué puedo ayudarte?',
    subMessage: 'La IA analiza los documentos de referencia para ofrecer respuestas precisas',
    suggestions: [
      'Resume los puntos clave de este documento',
      'Pregunta de ejemplo 1',
      'Pregunta de ejemplo 2',
      'Pregunta de ejemplo 3',
    ],
  },
  zhHant: {
    mainMessage: '有什麼可以幫您的嗎？',
    subMessage: 'AI 分析參考文件以提供準確的回答',
    suggestions: [
      '請摘要這份文件的重點',
      '範例問題 1',
      '範例問題 2',
    ],
  },
  zhHans: {
    mainMessage: '有什么可以帮您的吗？',
    subMessage: 'AI 分析参考文档以提供准确的回答',
    suggestions: [
      '总结公司信息安全制度的核心要求',
      '示例问题 1',
      '示例问题 2',
      '示例问题 3',
    ],
  },
};

// localStorage 캐시 키 (서버 응답 캐시 — 오프라인/즉시 렌더용).
// 기존 단일 설정 키(chatEmptyStateSettings)와 충돌하지 않도록 버전 접미사를 둔다.
const CACHE_KEY = 'chatEmptyStateSettings:v2';

// 검증 한도 (백엔드 empty_state_router의 _MAIN_MAX 등과 일치).
const MAIN_MAX = 100;
const SUB_MAX = 200;
const SUGGESTION_MAX = 200;
const SUGGESTIONS_MAX = 10;

/** 클라이언트 사전 검증 失败 에러 (韩语 메시지 배열 보유) */
export class ChatSettingsValidationError extends Error {
  errors: string[];
  constructor(errors: string[]) {
    super('settings validation failed');
    this.name = 'ChatSettingsValidationError';
    this.errors = errors;
  }
}

class ChatSettingsService {
  // 동기 즉시 렌더용 메모리 캐시 (localStorage보다 빠른 1차 캐시).
  private memoryCache: AllEmptyStateSettings | null = null;

  /** 단일 로케일 기본값 （副本）. */
  getDefaults(locale: MenuLocale): ChatEmptyStateSettings {
    return this.cloneSettings(DEFAULTS[locale]);
  }

  /** 전 로케일 기본값 （副本）. */
  private allDefaults(): AllEmptyStateSettings {
    const result = {} as AllEmptyStateSettings;
    for (const locale of MENU_LOCALES) {
      result[locale] = this.cloneSettings(DEFAULTS[locale]);
    }
    return result;
  }

  /** 설정 객체 깊은 복사 (suggestions 배열 공유 방지). */
  private cloneSettings(s: ChatEmptyStateSettings): ChatEmptyStateSettings {
    return {
      mainMessage: s.mainMessage,
      subMessage: s.subMessage,
      suggestions: [...s.suggestions],
    };
  }

  /**
   * 동기 캐시 조회 (즉시 렌더용): 메모리 → localStorage → 기본값.
   * 서버 호출 없이 즉시 반환하며, 최신값은 fetchAll()로 갱신한다.
   */
  getCachedAll(): AllEmptyStateSettings {
    if (this.memoryCache) {
      return this.memoryCache;
    }
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<AllEmptyStateSettings>;
        const merged = this.mergeWithDefaults(parsed);
        this.memoryCache = merged;
        return merged;
      }
    } catch (error) {
      logger.error('빈 화면 설정 캐시 로드 失败:', error);
    }
    return this.allDefaults();
  }

  /** 단일 로케일 설정 (동기, 캐시/기본값). */
  getSettings(locale: MenuLocale): ChatEmptyStateSettings {
    return this.cloneSettings(this.getCachedAll()[locale] ?? DEFAULTS[locale]);
  }

  /**
   * 서버에서 전 로케일 설정을 가져온다 (공개 GET).
   * 失败(오프라인/서버 미응답 등) 시 캐시/기본값으로 graceful 폴백한다.
   */
  async fetchAll(): Promise<AllEmptyStateSettings> {
    try {
      const url = `${getAdminAPIBaseURL()}/api/chat-empty-state`;
      const response = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = (await response.json()) as Partial<AllEmptyStateSettings>;
      const merged = this.mergeWithDefaults(data);
      this.writeCache(merged);
      return merged;
    } catch (error) {
      logger.error('빈 화면 설정 서버 조회 失败 (캐시/기본값 사용):', error);
      return this.getCachedAll();
    }
  }

  /**
   * 관리자 저장 (PUT). X-API-Key가 필요하다.
   * @throws ChatSettingsValidationError 클라이언트 사전 검증 失败
   * @throws MissingAdminKeyError 관리자 키 미설정
   * @throws Error HTTP/네트워크 오류
   */
  async saveSettings(
    locale: MenuLocale,
    settings: ChatEmptyStateSettings,
  ): Promise<ChatEmptyStateSettings> {
    const validationErrors = this.validateSettings(settings);
    if (validationErrors.length > 0) {
      throw new ChatSettingsValidationError(validationErrors);
    }
    const headers = this.adminHeaders();
    const url = `${getAdminAPIBaseURL()}/api/admin/chat-empty-state/${encodeURIComponent(locale)}`;
    const response = await fetch(url, {
      method: 'PUT',
      headers,
      body: JSON.stringify({
        mainMessage: settings.mainMessage,
        subMessage: settings.subMessage,
        suggestions: settings.suggestions,
      }),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`HTTP ${response.status}: ${text}`);
    }
    const saved = this.normalizeServerEntry((await response.json()) as Partial<ChatEmptyStateSettings>, locale);
    this.updateCacheLocale(locale, saved);
    return saved;
  }

  /**
   * 관리자 리셋 (DELETE → 서버 기본값). X-API-Key가 필요하다.
   * @throws MissingAdminKeyError 관리자 키 미설정
   * @throws Error HTTP/네트워크 오류
   */
  async resetSettings(locale: MenuLocale): Promise<ChatEmptyStateSettings> {
    const headers = this.adminHeaders();
    const url = `${getAdminAPIBaseURL()}/api/admin/chat-empty-state/${encodeURIComponent(locale)}`;
    const response = await fetch(url, { method: 'DELETE', headers });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`HTTP ${response.status}: ${text}`);
    }
    const def = this.normalizeServerEntry((await response.json()) as Partial<ChatEmptyStateSettings>, locale);
    this.updateCacheLocale(locale, def);
    return def;
  }

  /** 관리자 키 존재 여부(운영 설정 또는 배포 주입). */
  hasAdminKey(): boolean {
    return hasAdminApiKey();
  }

  /** 관리자 요청 헤더 구성(키 없으면 MissingAdminKeyError). */
  private adminHeaders(): Record<string, string> {
    return getRequiredAdminAuthHeaders();
  }

  /** 서버 응답(부분 형태 허용)을 안전한 ChatEmptyStateSettings로 정규화. */
  private normalizeServerEntry(
    entry: Partial<ChatEmptyStateSettings>,
    locale: MenuLocale,
  ): ChatEmptyStateSettings {
    const fallback = DEFAULTS[locale];
    return {
      mainMessage: typeof entry.mainMessage === 'string' ? entry.mainMessage : fallback.mainMessage,
      subMessage: typeof entry.subMessage === 'string' ? entry.subMessage : fallback.subMessage,
      suggestions: Array.isArray(entry.suggestions)
        ? entry.suggestions.filter((s): s is string => typeof s === 'string')
        : [...fallback.suggestions],
    };
  }

  /** 서버 응답(전 로케일)을 기본값과 병합해 누락 로케일/필드를 보강. */
  private mergeWithDefaults(data: Partial<AllEmptyStateSettings>): AllEmptyStateSettings {
    const result = this.allDefaults();
    for (const locale of MENU_LOCALES) {
      const item = data?.[locale];
      if (
        item &&
        typeof item.mainMessage === 'string' &&
        typeof item.subMessage === 'string' &&
        Array.isArray(item.suggestions)
      ) {
        result[locale] = {
          mainMessage: item.mainMessage,
          subMessage: item.subMessage,
          suggestions: item.suggestions.filter((s): s is string => typeof s === 'string'),
        };
      }
    }
    return result;
  }

  /** 메모리/localStorage 캐시에 전 로케일 설정을 기록. */
  private writeCache(all: AllEmptyStateSettings): void {
    this.memoryCache = all;
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(all));
    } catch (error) {
      logger.error('빈 화면 설정 캐시 저장 失败:', error);
    }
  }

  /** 단일 로케일만 갱신해 캐시에 반영. */
  private updateCacheLocale(locale: MenuLocale, settings: ChatEmptyStateSettings): void {
    const all = this.getCachedAll();
    all[locale] = this.cloneSettings(settings);
    this.writeCache(all);
  }

  /**
   * 설정 유효성 검사 (클라이언트 사전 검증).
   * OneRAG 기존 구조를 보존하여 사용자에게 보여줄 韩语 메시지를 직접 반환한다.
   *
   * @returns 에러 메시지 배열 (빈 배열이면 유효함)
   */
  validateSettings(
    settings: ChatEmptyStateSettings | ChatEmptyStateSettingsUpdateRequest,
  ): string[] {
    const errors: string[] = [];

    // mainMessage 검사
    if ('mainMessage' in settings) {
      if (!settings.mainMessage || typeof settings.mainMessage !== 'string') {
        errors.push('主要消息为必填项');
      } else if (settings.mainMessage.trim().length === 0) {
        errors.push('主要消息不能为空');
      } else if (settings.mainMessage.length > MAIN_MAX) {
        errors.push(`主要消息不能超过 ${MAIN_MAX} 字`);
      }
    }

    // subMessage 검사
    if ('subMessage' in settings) {
      if (!settings.subMessage || typeof settings.subMessage !== 'string') {
        errors.push('次要消息为必填项');
      } else if (settings.subMessage.trim().length === 0) {
        errors.push('次要消息不能为空');
      } else if (settings.subMessage.length > SUB_MAX) {
        errors.push(`次要消息不能超过 ${SUB_MAX} 字`);
      }
    }

    // suggestions 검사
    if ('suggestions' in settings) {
      if (!Array.isArray(settings.suggestions)) {
        errors.push('推荐问题必须是数组');
      } else if (settings.suggestions.length === 0) {
        errors.push('至少需要 1 个推荐问题');
      } else if (settings.suggestions.length > SUGGESTIONS_MAX) {
        errors.push(`推荐问题最多 ${SUGGESTIONS_MAX} 个`);
      } else {
        settings.suggestions.forEach((suggestion, index) => {
          if (typeof suggestion !== 'string') {
            errors.push(`推荐问题 ${index + 1} 必须是字符串`);
          } else if (suggestion.trim().length === 0) {
            errors.push(`推荐问题 ${index + 1} 不能为空`);
          } else if (suggestion.length > SUGGESTION_MAX) {
            errors.push(`推荐问题 ${index + 1} 不能超过 ${SUGGESTION_MAX} 字`);
          }
        });

        const uniqueSuggestions = new Set(settings.suggestions.map((s) => s.trim()));
        if (uniqueSuggestions.size !== settings.suggestions.length) {
          errors.push('存在重复的推荐问题');
        }
      }
    }

    return errors;
  }
}

export const chatSettingsService = new ChatSettingsService();
export default chatSettingsService;