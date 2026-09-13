import { a1Strategy } from './a1-strategy.ts';
import { a2Creative } from './a2-creative.ts';
import { a3Planner } from './a3-planner.ts';
import { a4Copywriter } from './a4-copywriter.ts';
import { a5Editor } from './a5-editor.ts';
import { a6FactChecker } from './a6-factchecker.ts';
import { a7Compliance } from './a7-compliance.ts';
import { a10Analyst } from './a10-analyst.ts';
import type { AgentDefinition } from './base.ts';
import type { AgentId } from '../core/types.ts';

/** 已实现的智能体（MVP 范围）。 */
export const AGENTS: Record<string, AgentDefinition> = {
  A1: a1Strategy,
  A2: a2Creative,
  A3: a3Planner,
  A4: a4Copywriter,
  A5: a5Editor,
  A6: a6FactChecker,
  A7: a7Compliance,
  A10: a10Analyst,
};

/** 规划中但尚未实现的智能体，用于在界面上展示路线图。 */
export const PLANNED_AGENTS: { id: AgentId; name: string; role: string; description: string }[] = [
  { id: 'A0', name: '总控编排智能体', role: '项目经理 / 总控', description: '拆解任务、调度智能体、控制流程与门禁（由编排引擎承担）' },
  { id: 'A8', name: '视觉美术指导智能体', role: '美术指导', description: '视觉风格、配图 Prompt、分镜与版式建议' },
  { id: 'A9', name: '渠道运营与 SEO 智能体', role: '运营 / SEO', description: '多平台版本适配、关键词布局、发布策略' },
  { id: 'A11', name: '记忆与知识库智能体', role: '知识管理', description: '品牌记忆、案例检索、版本归档与模板沉淀' },
];

export function getAgent(id: string): AgentDefinition | undefined {
  return AGENTS[id];
}

export function allAgentMeta() {
  return Object.values(AGENTS).map((agent) => ({
    ...agent.meta,
    implemented: true,
  }));
}
