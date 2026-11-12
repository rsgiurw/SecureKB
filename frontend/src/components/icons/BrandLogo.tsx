import React, { useEffect, useState } from 'react';
import { useIsDarkMode } from '../../hooks/useIsDarkMode';
import { BRAND_CONFIG } from '../../config/brand';
import { COLORS } from '../../config/colors';
import { useConfig } from '../../core/useConfig';
import { cn } from '@/lib/utils';

interface BrandLogoProps {
  width?: number | string;
  height?: number | string;
  className?: string;
  variant?: 'main' | 'icon'; // 'main': 主标志， 'icon': 아이콘만
}

const SVGLogoFallback: React.FC<{
  width: number | string;
  height: number | string;
  className?: string | undefined;
  isDark: boolean;
}> = ({
  width,
  height,
  className,
  isDark,
}) => {
    const mainColor = isDark ? COLORS.text.primary.dark : COLORS.text.primary.light;
    const secondaryColor = isDark ? COLORS.text.secondary.dark : COLORS.text.secondary.light;
    const lightColor = isDark ? COLORS.text.disabled.dark : COLORS.text.disabled.light;
    const bgColor = isDark ? COLORS.interactive.default.dark : COLORS.interactive.default.light;

    return (
      <svg
        width={width}
        height={height}
        viewBox="0 0 120 120"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className={className}
      >
        <circle
          cx="60"
          cy="60"
          r="55"
          fill={bgColor}
          stroke={mainColor}
          strokeWidth="2"
        />
        <g>
          <path
            d="M30 40 L30 80 Q30 85 35 85 L45 85"
            stroke={mainColor}
            strokeWidth="6"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M45 60 L75 60"
            stroke={secondaryColor}
            strokeWidth="6"
            strokeLinecap="round"
            fill="none"
          />
          <path
            d="M75 40 L75 80 Q75 85 80 85 L90 85"
            stroke={mainColor}
            strokeWidth="6"
            strokeLinecap="round"
            fill="none"
          />
          <circle cx="38" cy="50" r="3" fill={secondaryColor}>
            <animate
              attributeName="opacity"
              values="0.3;1;0.3"
              dur="2s"
              repeatCount="indefinite"
            />
          </circle>
          <circle cx="60" cy="60" r="4" fill={lightColor}>
            <animate
              attributeName="opacity"
              values="0.5;1;0.5"
              dur="2s"
              begin="0.3s"
              repeatCount="indefinite"
            />
          </circle>
          <circle cx="82" cy="70" r="3" fill={secondaryColor}>
            <animate
              attributeName="opacity"
              values="0.3;1;0.3"
              dur="2s"
              begin="0.6s"
              repeatCount="indefinite"
            />
          </circle>
        </g>
        <g opacity="0.8">
          <path
            d="M60 25 L61 28 L64 28 L61.5 30 L62.5 33 L60 31 L57.5 33 L58.5 30 L56 28 L59 28 Z"
            fill={lightColor}
          >
            <animateTransform
              attributeName="transform"
              attributeType="XML"
              type="rotate"
              from="0 60 28"
              to="360 60 28"
              dur="4s"
              repeatCount="indefinite"
            />
          </path>
        </g>
      </svg>
    );
  };

export const BrandLogo: React.FC<BrandLogoProps> = ({
  width = 40,
  height = 40,
  className,
  variant = 'main',
}) => {
  const [imageError, setImageError] = useState(false);
  const isDark = useIsDarkMode();
  const { config } = useConfig();
  const brandConfig = config.brand ?? BRAND_CONFIG;
  const logoConfig = brandConfig.logo;

  const style = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
  };

  useEffect(() => {
    setImageError(false);
  }, [logoConfig.dark, logoConfig.fallback, logoConfig.main, logoConfig.type, variant]);

  if (logoConfig.type === 'svg-component') {
    return <SVGLogoFallback width={width} height={height} className={className} isDark={isDark} />;
  }

  if (logoConfig.type === 'text') {
    // [FIX 设计评审 P1] text 品牌此前忽略 variant：icon 槽里渲染完整appName 文字
    // （10px 微缩 + 溢出裁切），旁边又渲染正式 wordmark → "SecureKBSecureKB" 双写。
    // icon variant 改渲染品牌首字母方块（深底白字，贴 Secure 定位）；
    // main/默认 variant 保持完整文字 wordmark。
    if (variant === 'icon') {
      const sizeNum = typeof height === 'number' ? height : 24;
      return (
        <div
          className={cn(
            'flex items-center justify-center rounded-lg bg-primary text-primary-foreground font-black select-none',
            className
          )}
          style={{ ...style, width: sizeNum, height: sizeNum, fontSize: sizeNum * 0.52 }}
          aria-hidden="true"
        >
          {brandConfig.appName.charAt(0).toUpperCase()}
        </div>
      );
    }
    return (
      <div
        className={cn("flex flex-col items-center justify-center font-bold tracking-tight", className)}
        style={{
          fontSize: typeof height === 'number' ? `${height * 0.45}px` : '1.25rem',
          color: 'var(--foreground)',
          ...style,
        }}
      >
        {brandConfig.appName}
      </div>
    );
  }

  const logoSrc = variant === 'main'
    ? (isDark && logoConfig.dark ? logoConfig.dark : logoConfig.main)
    : logoConfig.main;

  if (imageError || !logoSrc) {
    if (logoConfig.fallback) {
      return (
        <img
          src={logoConfig.fallback}
          alt={logoConfig.alt}
          style={style}
          className={cn("object-contain block", className)}
          onError={() => setImageError(false)}
        />
      );
    }
    return <SVGLogoFallback width={width} height={height} className={className} isDark={isDark} />;
  }

  return (
    <img
      src={logoSrc}
      alt={logoConfig.alt}
      style={style}
      className={cn("object-contain block", className)}
      onError={() => setImageError(true)}
    />
  );
};

export default BrandLogo;
