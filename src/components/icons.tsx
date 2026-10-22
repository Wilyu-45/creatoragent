/** 全站线性图标：统一 24 视口、currentColor 描边，避免用文字字母充当图标。 */
import type { ComponentType, ReactNode, SVGProps } from 'react';
import type { WorkbenchKey } from '../lib/workbenches.ts';

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'width' | 'height'> {
  size?: number;
  strokeWidth?: number;
}

function Glyph({ size = 18, strokeWidth = 1.8, children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export function LogoMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
      <path
        d="M12 2.6c.95 5.25 3.2 7.5 8.45 8.45-5.25.95-7.5 3.2-8.45 8.45-.95-5.25-3.2-7.5-8.45-8.45C8.8 10.1 11.05 7.85 12 2.6Z"
        fill="currentColor"
      />
      <circle cx="18.8" cy="18.6" r="2.3" fill="currentColor" opacity="0.72" />
    </svg>
  );
}

export function IconHome(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3.5 10.5 12 3.5l8.5 7" />
      <path d="M5.5 9.5V20a.8.8 0 0 0 .8.8h11.4a.8.8 0 0 0 .8-.8V9.5" />
      <path d="M9.6 20.8v-6h4.8v6" />
    </Glyph>
  );
}

export function IconCompass(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="8.7" />
      <path d="m15.6 8.4-2.1 5.1-5.1 2.1 2.1-5.1z" />
    </Glyph>
  );
}

export function IconLayers(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m12 3.2 8.5 4.6-8.5 4.6-8.5-4.6z" />
      <path d="m3.5 12.2 8.5 4.6 8.5-4.6" />
      <path d="m3.5 16.6 8.5 4.6 8.5-4.6" />
    </Glyph>
  );
}

export function IconClapperboard(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="3.2" y="9" width="17.6" height="11.3" rx="2" />
      <path d="m3.8 9 1.4-4.4 15.5 2.2-.5 2.2" />
      <path d="m9 5.1-.9 3.8M14.4 5.9l-.8 3.7M19.6 6.7l-.6 2.4" />
    </Glyph>
  );
}

export function IconSparkles(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M11 3.5c.7 4 2.4 5.7 6.4 6.4-4 .7-5.7 2.4-6.4 6.4-.7-4-2.4-5.7-6.4-6.4 4-.7 5.7-2.4 6.4-6.4Z" />
      <path d="M18.2 14.4c.4 2.1 1.3 3 3.4 3.4-2.1.4-3 1.3-3.4 3.4-.4-2.1-1.3-3-3.4-3.4 2.1-.4 3-1.3 3.4-3.4Z" />
    </Glyph>
  );
}

export function IconGauge(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.2 18.5a9 9 0 1 1 15.6 0" />
      <path d="m12 13.2 4.4-4.4" />
      <circle cx="12" cy="13.2" r="1.5" />
    </Glyph>
  );
}

export function IconWorkflow(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="3.2" y="3.6" width="6.4" height="5.2" rx="1.5" />
      <rect x="14.4" y="3.6" width="6.4" height="5.2" rx="1.5" />
      <rect x="8.8" y="15.2" width="6.4" height="5.2" rx="1.5" />
      <path d="M6.4 8.8v3.4h11.2V8.8M12 12.2v3" />
    </Glyph>
  );
}

export function IconPackage(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m12 2.8 8.2 4.6v9.2L12 21.2l-8.2-4.6V7.4z" />
      <path d="m3.8 7.4 8.2 4.6 8.2-4.6" />
      <path d="M12 12v9.2" />
    </Glyph>
  );
}

export function IconWand(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m3.6 20.4 9.3-9.3" />
      <path d="M15.4 3.4c.4 2.3 1.4 3.3 3.7 3.7-2.3.4-3.3 1.4-3.7 3.7-.4-2.3-1.4-3.3-3.7-3.7 2.3-.4 3.3-1.4 3.7-3.7Z" />
      <path d="m19.2 14.6.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" />
    </Glyph>
  );
}

export function IconSend(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M21 3 3.4 10.2l6.7 2.7 2.7 6.7z" />
      <path d="M21 3 10.1 12.9" />
    </Glyph>
  );
}

export function IconShieldCheck(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 2.9 19 6v5.1c0 4.4-2.9 7.5-7 9.1-4.1-1.6-7-4.7-7-9.1V6z" />
      <path d="m9 12 2.2 2.2L15.4 10" />
    </Glyph>
  );
}

export function IconActivity(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3 12h4l2.4-6 5.2 12 2.4-6h4" />
    </Glyph>
  );
}

export function IconImage(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="3.2" y="4.2" width="17.6" height="15.6" rx="2.2" />
      <circle cx="8.8" cy="9.6" r="1.6" />
      <path d="m4.2 18.4 5-5 3.7 3.7 3.1-3.1 4 4" />
    </Glyph>
  );
}

export function IconVideo(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="3" y="6" width="12.4" height="12" rx="2.2" />
      <path d="m15.4 10.2 5.6-3v9.6l-5.6-3z" />
    </Glyph>
  );
}

export function IconUser(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.7 20.8c.4-3.8 3.6-6.1 7.3-6.1s6.9 2.3 7.3 6.1" />
    </Glyph>
  );
}

export function IconMic(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="9" y="2.8" width="6" height="11.2" rx="3" />
      <path d="M5.6 11.4a6.4 6.4 0 0 0 12.8 0" />
      <path d="M12 17.8v3.4" />
    </Glyph>
  );
}

export function IconEye(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M2.8 12S6.4 5.8 12 5.8 21.2 12 21.2 12 17.6 18.2 12 18.2 2.8 12 2.8 12Z" />
      <circle cx="12" cy="12" r="3" />
    </Glyph>
  );
}

export function IconWaves(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3 8.4c2.8 0 2.8 3 5.6 3s2.8-3 5.6-3 2.8 3 5.6 3" />
      <path d="M3 14.2c2.8 0 2.8 3 5.6 3s2.8-3 5.6-3 2.8 3 5.6 3" />
    </Glyph>
  );
}

export function IconPaw(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="6.4" cy="10" r="1.7" />
      <circle cx="10" cy="6.4" r="1.7" />
      <circle cx="14" cy="6.4" r="1.7" />
      <circle cx="17.6" cy="10" r="1.7" />
      <path d="M12 11.2c2.9 0 5.4 1.9 5.4 4.4 0 1.9-1.5 3.4-3.4 3.4-.8 0-1.4-.3-2-.3s-1.2.3-2 .3c-1.9 0-3.4-1.5-3.4-3.4 0-2.5 2.5-4.4 5.4-4.4Z" />
    </Glyph>
  );
}

export function IconGraduation(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m2.8 9.1 9.2-5 9.2 5-9.2 5z" />
      <path d="M6.8 11.6v4.2c0 1.7 2.3 3 5.2 3s5.2-1.3 5.2-3v-4.2" />
      <path d="M21.2 9.1v5.2" />
    </Glyph>
  );
}

export function IconPlus(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 4.5v15M4.5 12h15" />
    </Glyph>
  );
}

export function IconMinus(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.5 12h15" />
    </Glyph>
  );
}

export function IconDownload(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 3.6v11.2" />
      <path d="m7.4 10.4 4.6 4.6 4.6-4.6" />
      <path d="M4.4 20.4h15.2" />
    </Glyph>
  );
}

export function IconExpand(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M9 4H4.4v4.6M15 4h4.6v4.6M9 20H4.4v-4.6M15 20h4.6v-4.6" />
    </Glyph>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="10.8" cy="10.8" r="6.6" />
      <path d="m15.7 15.7 4.4 4.4" />
    </Glyph>
  );
}

export function IconPlay(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M8.2 5.4v13.2L19 12z" />
    </Glyph>
  );
}

export function IconPause(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M8.4 5.2v13.6M15.6 5.2v13.6" strokeWidth="2.2" />
    </Glyph>
  );
}

export function IconRewind(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.2 5.6v12.8" />
      <path d="M20 6.4 9.8 12 20 17.6z" />
    </Glyph>
  );
}

export function IconRefresh(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M20.2 12a8.2 8.2 0 1 1-2.5-5.9" />
      <path d="M20.4 4.2v5h-5" />
    </Glyph>
  );
}

export function IconHistory(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3.8 12a8.2 8.2 0 1 0 2.4-5.8" />
      <path d="M3.6 4.4v5h5" />
      <path d="M12 7.4V12l3.1 1.9" />
    </Glyph>
  );
}

export function IconCompare(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="6.4" cy="6.2" r="2.6" />
      <circle cx="17.6" cy="17.8" r="2.6" />
      <path d="M6.4 8.8v5.1a3.9 3.9 0 0 0 3.9 3.9h4.7" />
      <path d="m13.2 15.8 2.1 2-2.1 2" />
      <path d="M17.6 15.2V10a3.9 3.9 0 0 0-3.9-3.9H9" />
      <path d="m11 4 2.1 2.1L11 8.2" />
    </Glyph>
  );
}

export function IconChart(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <path d="M8.4 20v-5.8M12.8 20V9.4M17.2 20v-8.2" strokeWidth="2" />
    </Glyph>
  );
}

export function IconVolume(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.2 9.4h3.6L13 5.2v13.6l-5.2-4.2H4.2z" />
      <path d="M16.2 9a4.4 4.4 0 0 1 0 6M18.8 6.4a8 8 0 0 1 0 11.2" />
    </Glyph>
  );
}

export function IconMuted(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.2 9.4h3.6L13 5.2v13.6l-5.2-4.2H4.2z" />
      <path d="m16.4 9.6 4 4.8M20.4 9.6l-4 4.8" />
    </Glyph>
  );
}

export function IconSettings(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4 7.2h7.2M16.4 7.2H20M4 16.8h3.6M12.8 16.8H20" />
      <circle cx="13.8" cy="7.2" r="2.2" />
      <circle cx="10.2" cy="16.8" r="2.2" />
    </Glyph>
  );
}

export function IconArrowRight(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4 12h15.5" />
      <path d="m13.8 6.2 5.7 5.8-5.7 5.8" />
    </Glyph>
  );
}

export function IconArrowLeft(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M20 12H4.5" />
      <path d="M10.2 6.2 4.5 12l5.7 5.8" />
    </Glyph>
  );
}

export function IconChevronRight(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m9.2 5.4 6.6 6.6-6.6 6.6" />
    </Glyph>
  );
}

export function IconCheck(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m4.8 12.4 4.8 4.8L19.2 7" />
    </Glyph>
  );
}

export function IconClose(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Glyph>
  );
}

export function IconCopy(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="8.5" y="8.5" width="11" height="11" rx="2" />
      <path d="M15.5 8.5V6A2.5 2.5 0 0 0 13 3.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5" />
    </Glyph>
  );
}

export function IconFolder(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3.5 7.4a2 2 0 0 1 2-2h3.4l2.1 2.3h7.5a2 2 0 0 1 2 2v7.2a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
      <path d="M3.5 10.4h17" />
    </Glyph>
  );
}

export function IconSave(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M5 4.5h10.8L19.5 8.2v11.3a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z" />
      <path d="M8 4.5v4.8h7V4.5" />
      <path d="M8 15.6h8" />
    </Glyph>
  );
}

export function IconUpload(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 16.2V4.6" />
      <path d="m7 9.4 5-4.8 5 4.8" />
      <path d="M4.5 15.2v3.4a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3.4" />
    </Glyph>
  );
}

export function IconTrash(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M4.5 7h15" />
      <path d="M9.5 7V5a1.2 1.2 0 0 1 1.2-1.2h2.6A1.2 1.2 0 0 1 14.5 5v2" />
      <path d="m6.6 7 .8 12a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5l.8-12" />
      <path d="M10.3 10.8v6.2M13.7 10.8v6.2" />
    </Glyph>
  );
}

const WORKBENCH_ICONS: Record<WorkbenchKey, ComponentType<IconProps>> = {
  tasks: IconCompass,
  factory: IconLayers,
  video: IconClapperboard,
  motion: IconSparkles,
};

export function WorkbenchIcon({ workbench, ...rest }: IconProps & { workbench: WorkbenchKey }) {
  const Cmp = WORKBENCH_ICONS[workbench];
  return <Cmp {...rest} />;
}
