/** 四个工作台的统一定位：首页入口、顶栏导航、工场页头共用同一份描述。 */
export type WorkbenchKey = 'tasks' | 'motion' | 'video' | 'factory';

export type AppView = 'home' | WorkbenchKey;

export type TaskSectionKey = 'overview' | 'pipeline' | 'artifacts' | 'tools' | 'deliver' | 'quality' | 'observe';

export interface WorkbenchMeta {
  key: WorkbenchKey;
  name: string;
  short: string;
  role: string;
  purpose: string;
  input: string;
  output: string;
  lane: string;
  accent: string;
}

export const WORKBENCHES: WorkbenchMeta[] = [
  {
    key: 'tasks',
    name: '创作任务工作台',
    short: '创作任务',
    role: '策划与审核中枢',
    purpose: '用一份 Brief 驱动 A1–A11 智能体：定策略、出创意、写文案与视频脚本，并完成门禁审核、发布和效果复盘。',
    input: 'Brief · 受众 · 渠道 · 关键词',
    output: '策略 · 文案 · 视频脚本 · 发布排期',
    lane: '链路起点 / 回流终点',
    accent: 'accent-plan',
  },
  {
    key: 'factory',
    name: '影视制作工厂',
    short: '影视制作工厂',
    role: '岗位化重生产',
    purpose: '把镜头与分镜拆成 60 个岗位节点，在同一张画布上完成画面、配音、剪辑，并打包为可下载的交付包。',
    input: '分镜脚本 · 镜头清单 · 岗位参数',
    output: '镜头资产 · 剪辑成片 · 交付包',
    lane: '产线 B · 重生产',
    accent: 'accent-produce',
  },
  {
    key: 'video',
    name: '视频工场',
    short: '视频工场',
    role: '字幕驱动快成片',
    purpose: '把字幕变成分幕动效成片：卡点切镜、BGM / 旁白混音，在浏览器里直接导出 MP4。',
    input: 'SRT / VTT / ASS 字幕 · BGM · 旁白',
    output: '分幕成片 · 720p / 1080p / 4K MP4',
    lane: '产线 A · 快成片',
    accent: 'accent-fast',
  },
  {
    key: 'motion',
    name: '动效工场',
    short: '动效工场',
    role: '包装支撑台',
    purpose: '试演标题、关键词、出场与转场动效，编排时间轴脚本，为两条产线提供包装参考与动效参数。',
    input: '标题 · 关键词 · 转场节奏',
    output: '动效预演 · JSON 时间轴脚本',
    lane: '横切支撑 · 不直接出片',
    accent: 'accent-motion',
  },
];

export function workbenchMeta(key: WorkbenchKey): WorkbenchMeta {
  return WORKBENCHES.find((item) => item.key === key) ?? WORKBENCHES[0];
}
