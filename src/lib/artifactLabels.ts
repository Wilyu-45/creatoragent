import type { Artifact, ArtifactType } from './types.ts';

export const TYPE_LABEL: Record<ArtifactType, string> = {
  task_plan: '任务卡与流程编排',
  strategy_brief: '策略洞察简报',
  creative_concept: '创意概念',
  content_plan: '内容策划案',
  copy_draft: '文案草稿',
  edited_copy: '编辑审校稿',
  fact_check_report: '事实核查报告',
  compliance_report: '品牌合规报告',
  visual_brief: '视觉美术指导',
  video_script: '视频脚本',
  channel_adaptation: '渠道适配与发布策略',
  publish_plan: '多平台发布排期',
  effect_report: '效果预估报告',
  knowledge_card: '知识沉淀卡片',
  document_digest: '素材研读要点',
  final_delivery: '最终交付物',
};

/** A10 的 effect_report 同时承载发布前预估与发布后复盘，列表里不能都叫「效果预估」。 */
export function artifactLabel(artifact: Artifact): string {
  if (artifact.type === 'effect_report' && artifact.content?.mode === 'post_publish_review') {
    return '发布后复盘报告';
  }
  return TYPE_LABEL[artifact.type] ?? artifact.type;
}

/** 类型筛选按 artifact.type 聚合，跨两种形态的类型给出合并名称。 */
export function artifactTypeLabel(type: ArtifactType): string {
  if (type === 'effect_report') return '效果预估 / 复盘报告';
  return TYPE_LABEL[type] ?? type;
}
