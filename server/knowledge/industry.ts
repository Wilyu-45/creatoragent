/**
 * 行业洞察库与爆款案例库。
 * 服务于 A1（策略洞察）与 A2（创意总监）的离线知识来源；
 * 生产环境应替换为 A11 + 向量数据库的 RAG 检索。
 */

export interface IndustryProfile {
  pain_points: string[];
  scenarios: string[];
  motivations: string[];
  objections: string[];
  proof_assets: string[];
}

export const INDUSTRY_PROFILES: Record<string, IndustryProfile> = {
  消费品: {
    pain_points: ['同类产品太多，不知道选哪个', '买回来发现和宣传不一样', '用起来麻烦、难以坚持'],
    scenarios: ['日常通勤路上', '周末居家整理', '朋友聚会分享', '换季囤货'],
    motivations: ['提升生活质感', '省时省心', '性价比高', '身边人都在用'],
    objections: ['是不是智商税', '价格是不是虚高', '和便宜款差别在哪'],
    proof_assets: ['实测对比图', '材质与工艺细节', '真实用户复购率', '第三方检测报告'],
  },
  美妆个护: {
    pain_points: ['肤质不适合、容易闷痘', '颜色上脸和试色不一样', '妆感厚重不自然', '持妆时间短'],
    scenarios: ['通勤伪素颜', '约会妆容', '重要会议前', '换季敏感期'],
    motivations: ['变美自信', '省时间', '被夸气色好', '成分安全'],
    objections: ['会不会闷痘', '敏感肌能用吗', '和爆款比谁更好'],
    proof_assets: ['素人实测前后对比', '成分表解读', '持妆时长实测', '皮肤刺激性测试'],
  },
  食品饮料: {
    pain_points: ['口味不稳定', '配料表看不懂', '热量负担', '买到手不新鲜'],
    scenarios: ['早餐来不及', '下午茶解馋', '加班充饥', '聚会分享'],
    motivations: ['好吃不罪恶', '方便快捷', '健康成分', '包装颜值'],
    objections: ['是不是科技与狠活', '甜不甜', '保质期多久'],
    proof_assets: ['配料表实拍', '口感盲测', '产地溯源', '营养检测数据'],
  },
  科技数码: {
    pain_points: ['参数看不懂、怕买错', '续航焦虑', '系统卡顿', '售后麻烦'],
    scenarios: ['办公提效', '通勤娱乐', '出差便携', '居家办公'],
    motivations: ['效率提升', '省心稳定', '追求新体验', '性价比'],
    objections: ['和上一代差别大吗', '是不是挤牙膏', '同价位竞品怎么选'],
    proof_assets: ['跑分与实测数据', '场景化体验视频', '拆机用料', '续航实测'],
  },
  医疗健康: {
    pain_points: ['体检指标异常不知从何下手', '反复发作', '方案看不懂', '担心副作用'],
    scenarios: ['年度体检后', '慢病日常管理', '康复阶段', '就医前咨询'],
    motivations: ['恢复健康', '少走弯路', '减少焦虑', '专业可信'],
    objections: ['有没有临床依据', '适合我吗', '会不会有副作用'],
    proof_assets: ['临床研究引用', '专家背书', '指南条款', '用户随访数据'],
  },
  教育培训: {
    pain_points: ['学了记不住', '不知道从哪开始', '坚持不下去', '效果看不见'],
    scenarios: ['备考冲刺', '转行准备', '碎片时间充电', '家长辅导'],
    motivations: ['拿证/提分', '职业跃迁', '自我提升', '少走弯路'],
    objections: ['有没有实际效果', '老师什么背景', '没时间跟得上吗'],
    proof_assets: ['学员成果案例', '教研体系说明', '课程大纲', '试听体验'],
  },
  企业服务: {
    pain_points: ['手工流程低效易错', '数据分散难汇总', '系统对接成本高', '上线周期长'],
    scenarios: ['月度对账', '多部门协同', '合规审计', '业务扩容'],
    motivations: ['降本增效', '风险可控', '快速上线', '可扩展'],
    objections: ['迁移成本多高', '数据安全吗', 'ROI 多久回本'],
    proof_assets: ['客户 ROI 数据', '安全合规认证', '实施方法论', '同行业标杆案例'],
  },
};

export const DEFAULT_INDUSTRY: IndustryProfile = INDUSTRY_PROFILES['消费品']!;

export function industryProfile(industry: string): IndustryProfile {
  if (INDUSTRY_PROFILES[industry]) return INDUSTRY_PROFILES[industry];
  const hit = Object.keys(INDUSTRY_PROFILES).find(
    (key) => industry.includes(key) || key.includes(industry),
  );
  return hit ? INDUSTRY_PROFILES[hit]! : DEFAULT_INDUSTRY;
}

/* ------------------------------------------------------------------ */
/* 渠道规则                                                            */
/* ------------------------------------------------------------------ */

export interface ChannelRule {
  format: string;
  length_hint: string;
  title_style: string;
  blocks: string[];
  hashtag_policy: string;
  compliance_notes: string[];
}

export const CHANNEL_RULES: Record<string, ChannelRule> = {
  小红书: {
    format: '图文笔记（封面 + 6-9 张图 + 正文）',
    length_hint: '正文 300-600 字',
    title_style: '20 字以内，含数字/身份锚点/情绪词，可用 emoji 分栏',
    blocks: ['封面标题', '身份锚点', '痛点共鸣', '使用场景', '产品亮点 3 条', '真实感受', '行动引导', '话题标签'],
    hashtag_policy: '5-10 个，2 个大词 + 3 个垂类词 + 2 个长尾词',
    compliance_notes: ['禁止使用绝对化用语', '禁止夸大功效', '商业推广需标注「合作」'],
  },
  抖音: {
    format: '短视频脚本（黄金 3 秒钩子 + 分镜 + 口播 + 字幕）',
    length_hint: '30-60 秒，口播 150-220 字',
    title_style: '18 字以内，冲突感/悬念感强',
    blocks: ['0-3s 钩子', '3-8s 痛点', '8-25s 解决方案', '25-40s 证据', '40-50s 转化'],
    hashtag_policy: '3-5 个，紧跟平台热点话题',
    compliance_notes: ['不得诱导点赞关注', '价格表述需与实际一致'],
  },
  公众号: {
    format: '长图文',
    length_hint: '1500-2500 字',
    title_style: '可双标题，信息量优先，善用冒号结构',
    blocks: ['标题', '导语钩子', '背景铺垫', '核心观点 3 段', '案例佐证', '结论与行动'],
    hashtag_policy: '不依赖标签，重视在看/转发引导',
    compliance_notes: ['转载需授权', '数据引用需标注来源'],
  },
  知乎: {
    format: '问答式长文',
    length_hint: '1000-2000 字',
    title_style: '问题式标题，专业克制',
    blocks: ['结论先行', '论据分层', '数据与引用', '反方观点回应', '总结'],
    hashtag_policy: '依赖话题绑定，2-4 个',
    compliance_notes: ['禁止利益相关未披露', '专业领域需资质说明'],
  },
  电商详情页: {
    format: '卖点结构化详情',
    length_hint: '5 屏核心卖点 + 参数表',
    title_style: '卖点短句，动宾结构',
    blocks: ['首屏主图利益点', '痛点场景', '核心卖点 3-5 条', '参数对比', '信任背书', '售后保障'],
    hashtag_policy: '不适用',
    compliance_notes: ['禁止价格欺诈', '不得虚构原价', '功效宣称需有依据'],
  },
  官网: {
    format: '品牌官网/落地页文案',
    length_hint: '首屏 20 字内主标题 + 3 条副标题',
    title_style: '品牌调性优先，克制有力',
    blocks: ['主标题', '副标题', '价值三支柱', '客户证言', 'CTA'],
    hashtag_policy: '不适用',
    compliance_notes: ['资质证书需真实可查'],
  },
  PR稿: {
    format: '新闻通稿',
    length_hint: '800-1200 字',
    title_style: '客观陈述，含主体与事件',
    blocks: ['标题', '导语（5W）', '背景', '核心信息', '高管引语', '关于我们'],
    hashtag_policy: '不适用',
    compliance_notes: ['不得使用广告法禁用语', '数据需可核实'],
  },
};

export function channelRule(channel: string): ChannelRule {
  if (CHANNEL_RULES[channel]) return CHANNEL_RULES[channel];
  const hit = Object.keys(CHANNEL_RULES).find((key) => channel.includes(key));
  return hit ? CHANNEL_RULES[hit]! : CHANNEL_RULES['小红书']!;
}

/* ------------------------------------------------------------------ */
/* 爆款案例库（A2 参考方向）                                            */
/* ------------------------------------------------------------------ */

export interface CaseEntry {
  name: string;
  industry: string;
  channel: string;
  angle: string;
  why: string;
  source: string;
}

export const CASE_LIBRARY: CaseEntry[] = [
  {
    name: '「成分党」拆解式种草',
    industry: '美妆个护',
    channel: '小红书',
    angle: '把成分表逐条翻译成消费者语言，用「不推荐谁」建立可信度',
    why: '反向筛选比正向推销更易建立专业信任，评论区争议带来二次曝光',
    source: '公开投放案例复盘（小红书商业化案例库）',
  },
  {
    name: '「打工人 5 分钟」场景锚定',
    industry: '食品饮料',
    channel: '抖音',
    angle: '把产品嵌进一个高频、具体的日常场景，用时间长度量化价值',
    why: '场景越具体，代入感越强，完播率显著高于泛化表达',
    source: '短视频行业内容方法论（公开分享）',
  },
  {
    name: '「实测数据可视化」硬核证明',
    industry: '科技数码',
    channel: '知乎',
    angle: '用可复现的实测对比替代形容词，主动暴露一个小缺点',
    why: '主动示弱提升整体可信度，实测数据带来长尾搜索流量',
    source: '消费电子评测内容实践',
  },
  {
    name: '「用户证言前置」信任置换',
    industry: '教育培训',
    channel: '公众号',
    angle: '开篇即抛出真实学员的转折故事，把品牌主张藏在结果里',
    why: '故事结构的记忆留存率高于观点罗列，转发意愿更强',
    source: '内容营销经典结构',
  },
  {
    name: '「反常识提问」钩子',
    industry: '企业服务',
    channel: 'PR稿',
    angle: '用一个行业普遍误区作为标题，正文用数据推翻',
    why: '反常识制造认知缺口，打开率明显高于平铺直叙的新闻稿',
    source: 'B2B 内容营销实践',
  },
  {
    name: '「成分/参数透明化」',
    industry: '消费品',
    channel: '电商详情页',
    angle: '把成本结构与参数来源摊开讲，用透明度对抗低价竞争',
    why: '透明度直接回应「是不是智商税」这一核心异议',
    source: '电商转化率优化实践',
  },
];

export function casesFor(industry: string, channel: string): CaseEntry[] {
  const scored = CASE_LIBRARY.map((entry) => {
    let score = 0;
    if (entry.industry === industry) score += 3;
    else if (entry.industry.includes(industry) || industry.includes(entry.industry)) score += 1;
    if (entry.channel === channel) score += 2;
    return { entry, score };
  }).sort((a, b) => b.score - a.score);
  return scored.slice(0, 3).map((s) => s.entry);
}
