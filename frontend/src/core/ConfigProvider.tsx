/**
 * ConfigProvider
 *
 * 런타임 설정을 관리하고 적용하는 Provider
 * localStorage에서 사용자 설정을 로드하여 앱에 적용합니다.
 */

import React, { useState, useEffect, ReactNode } from 'react';
import { APP_CONFIG, mergeConfig, type AppConfig } from '../config';
import { applyPreset, applyPresetCssVars, clearPresetCssVars } from '../config/presets';
import { ConfigContext, type RuntimeConfig } from './ConfigContext';
import { logger } from '../utils/logger';
import {
  applyOperatorRuntimeSettings,
  buildOperatorRuntimeConfig,
  hasStoredOperatorSettings,
  readOperatorSettings,
} from '../config/operatorSettings';

interface ConfigProviderProps {
  children: ReactNode;
}

export const ConfigProvider: React.FC<ConfigProviderProps> = ({ children }) => {
  const [config, setConfig] = useState<AppConfig>(APP_CONFIG);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);

  useEffect(() => {
    // localStorage에서 사용자 설정 로드
    const loadRuntimeConfig = () => {
      try {
        const saved = localStorage.getItem('customSettings');
        const operatorRuntimeConfig = hasStoredOperatorSettings()
          ? buildOperatorRuntimeConfig(readOperatorSettings())
          : null;

        if (!saved) {
          if (operatorRuntimeConfig) {
            setRuntimeConfig(operatorRuntimeConfig);
            applyRuntimeConfig(operatorRuntimeConfig);
            return;
          }

          logger.debug('[ConfigProvider] localStorage에 저장된 설정이 없습니다.');
          return;
        }

        const parsed: RuntimeConfig = JSON.parse(saved);
        let runtime: RuntimeConfig = parsed;
        if (operatorRuntimeConfig) {
          const brand = parsed.brand ?? operatorRuntimeConfig.brand;
          runtime = {
            ...parsed,
            operator: parsed.operator ?? operatorRuntimeConfig.operator,
            ...(brand ? { brand } : {}),
            features: parsed.features ?? operatorRuntimeConfig.features,
          };
        }
        logger.debug('[ConfigProvider] 저장된 설정 로드 完成');

        setRuntimeConfig(runtime);

        // 설정 적용
        applyRuntimeConfig(runtime);
      } catch (error) {
        logger.error('[ConfigProvider] 런타임 설정 로드 失败:', error);
      }
    };

    loadRuntimeConfig();
  }, []);

  const applyRuntimeConfig = (runtime: RuntimeConfig) => {
    let updatedConfig = { ...APP_CONFIG };

    // 운영 설정 적용
    if (runtime.operator) {
      applyOperatorRuntimeSettings(runtime.operator);
    }

    // 색상 프리셋 적용
    if (runtime.preset) {
      // [FIX 主题预设未生效] 合并进 Context 的 colors 此前无任何消费方——
      // 真正让界面变色的是把预设写入 shadcn CSS 变量（--primary 等）。
      applyPresetCssVars(runtime.preset);
      const presetColors = applyPreset(runtime.preset);

      if (presetColors) {
        const mergedColors = mergeConfig(updatedConfig.colors, presetColors);
        updatedConfig = { ...updatedConfig, colors: mergedColors };
      } else {
        logger.error(`[ConfigProvider] 프리셋 "${runtime.preset}" 찾을 수 无`);
      }
    }

    // 브랜드 설정 적용
    if (runtime.brand) {
      updatedConfig = mergeConfig(updatedConfig, { brand: runtime.brand });
    }

    // 레이아웃 설정 적용
    if (runtime.layout) {
      updatedConfig = mergeConfig(updatedConfig, { layout: runtime.layout });
    }

    // 기능 플래그 적용
    if (runtime.features) {
      updatedConfig = mergeConfig(updatedConfig, { features: runtime.features });

      // window.RUNTIME_CONFIG 업데이트 (FeatureProvider와 연동)
      if (typeof window !== 'undefined') {
        window.RUNTIME_CONFIG = window.RUNTIME_CONFIG || {};
        window.RUNTIME_CONFIG.FEATURES = runtime.features;
      }
    }

    setConfig(updatedConfig);
  };

  const updateConfig = (newConfig: RuntimeConfig) => {
    try {
      localStorage.setItem('customSettings', JSON.stringify(newConfig));
      setRuntimeConfig(newConfig);
      applyRuntimeConfig(newConfig);
      logger.debug('[ConfigProvider] 설정 업데이트 完成');
    } catch (error) {
      logger.error('[ConfigProvider] 설정 업데이트 失败:', error);
    }
  };

  const resetConfig = () => {
    try {
      localStorage.removeItem('customSettings');
      setRuntimeConfig(null);
      setConfig(APP_CONFIG);
      // [FIX 主题预设未生效] 重置时移除注入的预设 CSS 变量，回落底板配色
      clearPresetCssVars();
      logger.debug('[ConfigProvider] 설정 초기화 完成');
    } catch (error) {
      logger.error('[ConfigProvider] 설정 초기화 失败:', error);
    }
  };

  return (
    <ConfigContext.Provider value={{ config, runtimeConfig, updateConfig, resetConfig }}>
      {children}
    </ConfigContext.Provider>
  );
};

export default ConfigProvider;
