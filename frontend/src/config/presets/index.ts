/* eslint-disable no-restricted-syntax */
/**
 * 테마 프리셋 시스템
 *
 * 다양한 브랜드 색상 조합을 미리 정의하여 쉽게 적용할 수 있도록 합니다.
 * 고객사별로 적합한 프리셋을 선택하거나 커스터마이징할 수 있습니다.
 *
 * 이 파일은 색상 프리셋 정의를 위해 hex/rgba 색상을 사용합니다.
 */

import type { ColorConfig } from '../colors';
import type { DeepPartial } from '../deepPartial';

/**
 * 테마 프리셋 인터페이스
 */
export interface ThemePreset {
  id: string;
  name: string;
  description: string;
  colors: DeepPartial<ColorConfig>;
  preview: {
    primaryColor: string;
    secondaryColor: string;
    accentColor: string;
  };
}

/**
 * 테마 프리셋 목록
 */
export const THEME_PRESETS: Record<string, ThemePreset> = {
  // 기본 单色 (현재 설정)
  monotone: {
    id: 'monotone',
    name: '单色',
    description: '简洁的黑白设计，呈现专业感',
    colors: {
      brand: {
        primary: {
          light: 'rgba(0, 0, 0, 0.8)',
          dark: 'rgba(255, 255, 255, 0.9)',
        },
        secondary: {
          light: 'rgba(0, 0, 0, 0.6)',
          dark: 'rgba(255, 255, 255, 0.7)',
        },
        accent: {
          light: 'rgba(0, 0, 0, 0.4)',
          dark: 'rgba(255, 255, 255, 0.5)',
        },
      },
    },
    preview: {
      primaryColor: '#000000',
      secondaryColor: '#666666',
      accentColor: '#999999',
    },
  },

  // 现代蓝
  modernBlue: {
    id: 'modernBlue',
    name: '现代蓝',
    description: '值得信赖的蓝色调，适合企业形象',
    colors: {
      brand: {
        primary: {
          light: '#2196f3',
          dark: '#42a5f5',
        },
        secondary: {
          light: '#1976d2',
          dark: '#1e88e5',
        },
        accent: {
          light: '#03a9f4',
          dark: '#29b6f6',
        },
      },
      interactive: {
        default: {
          light: 'rgba(33, 150, 243, 0.08)',
          dark: 'rgba(66, 165, 245, 0.12)',
        },
        hover: {
          light: 'rgba(33, 150, 243, 0.12)',
          dark: 'rgba(66, 165, 245, 0.16)',
        },
        active: {
          light: 'rgba(33, 150, 243, 0.16)',
          dark: 'rgba(66, 165, 245, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#2196f3',
      secondaryColor: '#1976d2',
      accentColor: '#03a9f4',
    },
  },

  // 商务绿
  corporateGreen: {
    id: 'corporateGreen',
    name: '商务绿',
    description: '环保且稳定的绿色调',
    colors: {
      brand: {
        primary: {
          light: '#4caf50',
          dark: '#66bb6a',
        },
        secondary: {
          light: '#388e3c',
          dark: '#43a047',
        },
        accent: {
          light: '#8bc34a',
          dark: '#9ccc65',
        },
      },
      interactive: {
        default: {
          light: 'rgba(76, 175, 80, 0.08)',
          dark: 'rgba(102, 187, 106, 0.12)',
        },
        hover: {
          light: 'rgba(76, 175, 80, 0.12)',
          dark: 'rgba(102, 187, 106, 0.16)',
        },
        active: {
          light: 'rgba(76, 175, 80, 0.16)',
          dark: 'rgba(102, 187, 106, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#4caf50',
      secondaryColor: '#388e3c',
      accentColor: '#8bc34a',
    },
  },

  // 典雅紫
  elegantPurple: {
    id: 'elegantPurple',
    name: '典雅紫',
    description: '高贵且富有创意的紫色调',
    colors: {
      brand: {
        primary: {
          light: '#9c27b0',
          dark: '#ab47bc',
        },
        secondary: {
          light: '#7b1fa2',
          dark: '#8e24aa',
        },
        accent: {
          light: '#ba68c8',
          dark: '#ce93d8',
        },
      },
      interactive: {
        default: {
          light: 'rgba(156, 39, 176, 0.08)',
          dark: 'rgba(171, 71, 188, 0.12)',
        },
        hover: {
          light: 'rgba(156, 39, 176, 0.12)',
          dark: 'rgba(171, 71, 188, 0.16)',
        },
        active: {
          light: 'rgba(156, 39, 176, 0.16)',
          dark: 'rgba(171, 71, 188, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#9c27b0',
      secondaryColor: '#7b1fa2',
      accentColor: '#ba68c8',
    },
  },

  // 暖橙
  warmOrange: {
    id: 'warmOrange',
    name: '暖橙',
    description: '温暖活泼的橙色调',
    colors: {
      brand: {
        primary: {
          light: '#ff9800',
          dark: '#ffa726',
        },
        secondary: {
          light: '#f57c00',
          dark: '#fb8c00',
        },
        accent: {
          light: '#ffb74d',
          dark: '#ffcc80',
        },
      },
      interactive: {
        default: {
          light: 'rgba(255, 152, 0, 0.08)',
          dark: 'rgba(255, 167, 38, 0.12)',
        },
        hover: {
          light: 'rgba(255, 152, 0, 0.12)',
          dark: 'rgba(255, 167, 38, 0.16)',
        },
        active: {
          light: 'rgba(255, 152, 0, 0.16)',
          dark: 'rgba(255, 167, 38, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#ff9800',
      secondaryColor: '#f57c00',
      accentColor: '#ffb74d',
    },
  },

  // 专业灰
  professionalGray: {
    id: 'professionalGray',
    name: '专业灰',
    description: '沉稳专业的灰色调',
    colors: {
      brand: {
        primary: {
          light: '#607d8b',
          dark: '#78909c',
        },
        secondary: {
          light: '#455a64',
          dark: '#546e7a',
        },
        accent: {
          light: '#90a4ae',
          dark: '#b0bec5',
        },
      },
      interactive: {
        default: {
          light: 'rgba(96, 125, 139, 0.08)',
          dark: 'rgba(120, 144, 156, 0.12)',
        },
        hover: {
          light: 'rgba(96, 125, 139, 0.12)',
          dark: 'rgba(120, 144, 156, 0.16)',
        },
        active: {
          light: 'rgba(96, 125, 139, 0.16)',
          dark: 'rgba(120, 144, 156, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#607d8b',
      secondaryColor: '#455a64',
      accentColor: '#90a4ae',
    },
  },

  // 活力红
  vibrantRed: {
    id: 'vibrantRed',
    name: '活力红',
    description: '强烈热情的红色调',
    colors: {
      brand: {
        primary: {
          light: '#f44336',
          dark: '#ef5350',
        },
        secondary: {
          light: '#d32f2f',
          dark: '#e53935',
        },
        accent: {
          light: '#ff5252',
          dark: '#ff6659',
        },
      },
      interactive: {
        default: {
          light: 'rgba(244, 67, 54, 0.08)',
          dark: 'rgba(239, 83, 80, 0.12)',
        },
        hover: {
          light: 'rgba(244, 67, 54, 0.12)',
          dark: 'rgba(239, 83, 80, 0.16)',
        },
        active: {
          light: 'rgba(244, 67, 54, 0.16)',
          dark: 'rgba(239, 83, 80, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#f44336',
      secondaryColor: '#d32f2f',
      accentColor: '#ff5252',
    },
  },

  // 青碧
  tealCyan: {
    id: 'tealCyan',
    name: '青碧',
    description: '清新现代的青色调',
    colors: {
      brand: {
        primary: {
          light: '#009688',
          dark: '#26a69a',
        },
        secondary: {
          light: '#00796b',
          dark: '#00897b',
        },
        accent: {
          light: '#4db6ac',
          dark: '#80cbc4',
        },
      },
      interactive: {
        default: {
          light: 'rgba(0, 150, 136, 0.08)',
          dark: 'rgba(38, 166, 154, 0.12)',
        },
        hover: {
          light: 'rgba(0, 150, 136, 0.12)',
          dark: 'rgba(38, 166, 154, 0.16)',
        },
        active: {
          light: 'rgba(0, 150, 136, 0.16)',
          dark: 'rgba(38, 166, 154, 0.24)',
        },
      },
    },
    preview: {
      primaryColor: '#009688',
      secondaryColor: '#00796b',
      accentColor: '#4db6ac',
    },
  },
};

/**
 * 프리셋 ID 목록
 */
export const PRESET_IDS = Object.keys(THEME_PRESETS);

/**
 * 프리셋 조회
 */
export function getPreset(presetId: string): ThemePreset | undefined {
  return THEME_PRESETS[presetId];
}

/**
 * 모든 프리셋 목록 조회
 */
export function getAllPresets(): ThemePreset[] {
  return Object.values(THEME_PRESETS);
}

/**
 * 프리셋 적용
 *
 * 주의: 이 함수는 색상 설정만 반환합니다.
 * 실제 적용은 APP_CONFIG를 업데이트하거나 런타임 설정 로드를 통해 이루어집니다.
 */
export function applyPreset(presetId: string): DeepPartial<ColorConfig> | null {
  console.log(`🎨 [applyPreset] 프리셋 "${presetId}" 조회 중...`);
  const preset = getPreset(presetId);

  if (!preset) {
    console.error(`❌ [applyPreset] 프리셋을 찾을 수 없습니다: ${presetId}`);
    console.log('📋 [applyPreset] 사용 가능한 프리셋:', PRESET_IDS);
    return null;
  }

  console.log(`✅ [applyPreset] 프리셋 "${presetId}" 찾음:`, preset);
  console.log(`🎨 [applyPreset] 반환할 색상:`, preset.colors);

  return preset.colors;
}

/**
 * 프리셋을 JSON 형식으로 내보내기
 *
 * public/config/ 폴더에 저장할 수 있는 JSON 형식으로 변환합니다.
 */
export function exportPresetAsJSON(presetId: string): string | null {
  const colors = applyPreset(presetId);
  if (!colors) {
    return null;
  }

  const config = {
    colors,
  };

  return JSON.stringify(config, null, 2);
}

/* ==========================================================================
 * [FIX 主题预设未生效] 预设 → shadcn CSS 变量接线
 *
 * 原状：SettingsPage 保存 preset 到 localStorage，ConfigProvider 把预设色合并进
 * Context 的 config.colors —— 但全代码库无任何组件消费 config.colors，也没有一处
 * setProperty。颜色令牌实际走 index.css 的 `--primary: "H S% L%"`（Tailwind
 * hsl(var(--primary))）。预设因此"能选、能存、永远不变色"（已实现未接线）。
 *
 * 接线方式：注入一个 <style id="securekb-preset-vars">，包含 :root 与 .dark 两块，
 * 追加在 head 末尾（晚于 index.css，同特异度下后者胜出）。重置时移除该节点即可
 * 回落到底板配色。
 * ========================================================================== */

const PRESET_STYLE_ELEMENT_ID = 'securekb-preset-vars';

/** hex(#rgb/#rrggbb) 或 rgba(r,g,b,a) → {r,g,b}；解析失败返回 null。 */
function parseColorToRgb(input: string): { r: number; g: number; b: number } | null {
  const value = input.trim().toLowerCase();
  const hexMatch = /^#([0-9a-f]{3}|[0-9a-f]{6})$/.exec(value);
  const hexRaw = hexMatch?.[1];
  if (hexRaw) {
    const hex = hexRaw.length === 3
      ? hexRaw.split('').map((ch) => ch + ch).join('')
      : hexRaw;
    return {
      r: parseInt(hex.slice(0, 2), 16),
      g: parseInt(hex.slice(2, 4), 16),
      b: parseInt(hex.slice(4, 6), 16),
    };
  }
  const rgbaMatch = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/.exec(value);
  if (rgbaMatch?.[1] && rgbaMatch[2] && rgbaMatch[3]) {
    return {
      r: Number(rgbaMatch[1]),
      g: Number(rgbaMatch[2]),
      b: Number(rgbaMatch[3]),
    };
  }
  return null;
}

/** RGB → shadcn 令牌格式 "H S% L%"（HSL 分量、无 hsl() 包裹）。 */
function rgbToHslToken({ r, g, b }: { r: number; g: number; b: number }): string {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rn) h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6;
    else if (max === gn) h = ((bn - rn) / d + 2) / 6;
    else h = ((rn - gn) / d + 4) / 6;
  }
  return `${Math.round(h * 360)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%`;
}

/** 按相对亮度选前景色：亮底配深字、深底配白字。 */
function contrastForegroundToken({ r, g, b }: { r: number; g: number; b: number }): string {
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.55 ? '220.9 39.3% 11%' : '210 20% 98%';
}

/**
 * 把预设品牌色注入 CSS 变量（--primary/--secondary/--accent/--ring 及其前景色）。
 * light/dark 分别取预设的 light/dark 变体；无对应变体时跳过该项。
 */
export function applyPresetCssVars(presetId: string): boolean {
  if (typeof document === 'undefined') return false;
  const preset = getPreset(presetId);
  if (!preset) return false;

  const brand = preset.colors.brand as
    | { primary?: Record<'light' | 'dark', string>; secondary?: Record<'light' | 'dark', string>; accent?: Record<'light' | 'dark', string> }
    | undefined;
  if (!brand) return false;

  const buildBlock = (variant: 'light' | 'dark'): string => {
    const decls: string[] = [];
    const primary = brand.primary?.[variant] ? parseColorToRgb(brand.primary[variant]) : null;
    if (primary) {
      decls.push(`--primary: ${rgbToHslToken(primary)}`);
      decls.push(`--primary-foreground: ${contrastForegroundToken(primary)}`);
      decls.push(`--ring: ${rgbToHslToken(primary)}`);
    }
    const secondary = brand.secondary?.[variant] ? parseColorToRgb(brand.secondary[variant]) : null;
    if (secondary) {
      decls.push(`--secondary: ${rgbToHslToken(secondary)}`);
      decls.push(`--secondary-foreground: ${contrastForegroundToken(secondary)}`);
    }
    const accent = brand.accent?.[variant] ? parseColorToRgb(brand.accent[variant]) : null;
    if (accent) {
      decls.push(`--accent: ${rgbToHslToken(accent)}`);
      decls.push(`--accent-foreground: ${contrastForegroundToken(accent)}`);
    }
    return decls.join('; ');
  };

  const lightBlock = buildBlock('light');
  const darkBlock = buildBlock('dark');
  let styleEl = document.getElementById(PRESET_STYLE_ELEMENT_ID) as HTMLStyleElement | null;
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = PRESET_STYLE_ELEMENT_ID;
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = [
    lightBlock ? `:root { ${lightBlock} }` : '',
    darkBlock ? `.dark { ${darkBlock} }` : '',
  ].filter(Boolean).join('\n');
  return true;
}

/** 移除预设变量注入，回落到底板 index.css 配色（设置重置时调用）。 */
export function clearPresetCssVars(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(PRESET_STYLE_ELEMENT_ID)?.remove();
}
