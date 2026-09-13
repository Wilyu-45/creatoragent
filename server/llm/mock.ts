import type { Brief } from '../core/types.ts';
import { channelRule, casesFor, industryProfile } from '../knowledge/industry.ts';
import { autoRewrite, checkBrandVoice, scanCompliance } from '../knowledge/compliance.ts';
import { obj, objArray, str, strArray } from './json.ts';
import { estimateTokens } from './types.ts';
import type { LLMProvider, LLMRequest, LLMResponse } from './types.ts';

/**
 * 内置离线生成引擎（Mock Provider）
 *
 * 目标：在没有任何模型密钥的情况下，让「Brief → 交付物」的完整链路真实可跑，
 * 并且产出结构、字段名、门禁信号与真实模型完全一致 —— 切换到真实模型时上层无需改动。
 *
 * 它不是随机文本生成器，而是「规则 + 知识库」的创作引擎：
 *   - A1/A2/A3 从行业洞察库与案例库取材
 *   - A4 按渠道模板组装可读文案，并在无返工意见时故意保留风险表达（用于演示门禁）
 *   - A5/A6/A7 的评审信号来自真实可测量的文本特征与合规词库扫描
 */

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */

function rec(value: unknown): Record<string, unknown> {
  return obj(value);
}

function asBrief(ctx: Record<string, unknown>): Brief {
  const raw = rec(ctx.brief);
  return {
    brand: str(raw.brand, '品牌'),
    product: str(raw.product, '产品'),
    objective: str(raw.objective, '曝光'),
    audience: str(raw.audience, '目标人群'),
    channel: str(raw.channel, '小红书'),
    tone: str(raw.tone, '轻松、真实'),
    industry: str(raw.industry, '消费品'),
    keywords: strArray(raw.keywords),
    constraints: strArray(raw.constraints),
    deliverables: strArray(raw.deliverables),
    notes: str(raw.notes),
    priority: (str(raw.priority, 'normal') as Brief['priority']),
    deadline: typeof raw.deadline === 'string' ? raw.deadline : null,
  };
}

function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

function pick<T>(list: T[], seed: number, offset = 0): T {
  return list[(seed + offset) % list.length]!;
}

function pickMany<T>(list: T[], count: number, seed: number): T[] {
  const out: T[] = [];
  for (let i = 0; i < count && i < list.length; i += 1) {
    out.push(list[(seed + i * 7) % list.length]!);
  }
  return out;
}

function shorten(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function industryOf(brief: Brief): ReturnType<typeof industryProfile> {
  return industryProfile(brief.industry);
}

/* ------------------------------------------------------------------ */
/* A1 策略与洞察                                                       */
/* ------------------------------------------------------------------ */

function generateStrategy(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const profile = industryOf(brief);
  const seed = hash(`${brief.brand}${brief.product}${brief.industry}`);
  const keyword = brief.keywords[0] ?? brief.industry;

  const painPoints = pickMany(profile.pain_points, 3, seed);
  const scenarios = pickMany(profile.scenarios, 3, seed + 2);
  const motivations = pickMany(profile.motivations, 3, seed + 5);

  return {
    audience_profile: {
      segment: brief.audience,
      size_hint: `按行业公开口径，${brief.industry}中该人群属于核心决策与传播人群`,
      pain_points: painPoints,
      scenarios,
      motivations,
      objections: pickMany(profile.objections, 2, seed + 1),
    },
    message_house: {
      proposition: `${brief.brand}让${brief.audience}在${scenarios[0]}时，用更省心的方式获得${motivations[0]}`,
      support_points: [
        `围绕「${keyword}」建立可感知的差异点`,
        `${brief.product}的核心能力直接对应${painPoints[0]}`,
        `在${brief.channel}语境下形成可复述的记忆点`,
      ],
      benefits: [
        `解决「${painPoints[0]}」`,
        `在${scenarios[1]}场景下减少决策成本`,
        `满足对${motivations[1]}的期待`,
      ],
      evidence: pickMany(profile.proof_assets, 3, seed + 3).map((asset, index) => ({
        type: asset,
        status: index === 0 ? '需补充素材' : '待采集',
        note: index === 0 ? '建议在正式投放前补齐实测素材' : '可在发布前由品牌方提供',
      })),
    },
    objectives: [
      { type: brief.objective, metric: brief.channel === '小红书' ? '互动率 / 收藏率' : 'CTR', target: '高于账号近 30 天均值' },
      { type: '信任', metric: '评论正向占比', target: '≥ 70%' },
    ],
    channel_priority: [
      { channel: brief.channel, weight: 0.6, why: '与目标人群主活跃阵地一致，内容形态匹配度最高' },
      { channel: brief.channel === '小红书' ? '抖音' : '小红书', weight: 0.3, why: '同人群跨平台二次触达，复用素材成本低' },
      { channel: '公众号', weight: 0.1, why: '承接深度内容与品牌资产沉淀' },
    ],
    tone_guide: {
      keywords: brief.tone.split(/[、,，]/).map((t) => t.trim()).filter(Boolean),
      avoid: ['绝对化用语', '夸大功效', '居高临下的说教口吻'],
    },
    key_takeaways: [
      `核心人群诉求集中在「${painPoints[0]}」`,
      `内容需在${brief.channel}语境下先建立可信度，再谈卖点`,
      `「${motivations[0]}」是比参数更有力的说服角度`,
    ],
    confidence: 0.82,
    risks: [
      '受众画像基于行业公开经验推断，未经一手用户调研验证',
      `${profile.proof_assets[0]}等关键证据素材尚未提供，可能影响说服力`,
    ],
    evidence: [
      {
        claim: `${brief.industry}人群主要痛点集中在「${painPoints[0]}」`,
        source: `行业内容经验库（${brief.industry}）`,
        reliability: 0.7,
      },
      {
        claim: `${brief.channel}的内容形态为「${channelRule(brief.channel).format}」`,
        source: '平台公开规则与运营实践',
        reliability: 0.9,
      },
    ],
  };
}

/* ------------------------------------------------------------------ */
/* A2 创意总监                                                         */
/* ------------------------------------------------------------------ */

function generateCreative(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const strategy = rec(ctx.strategy);
  const house = rec(strategy.message_house);
  const audience = rec(strategy.audience_profile);
  const seed = hash(`${brief.brand}${brief.channel}${brief.tone}${brief.product}`);
  const pain = strArray(audience.pain_points)[0] ?? '选择成本高';
  const scenario = strArray(audience.scenarios)[0] ?? '日常使用';
  const proposition = str(house.proposition, `${brief.brand}的${brief.product}`);
  const cases = casesFor(brief.industry, brief.channel);

  const directions = [
    {
      id: 'D1',
      name: '真实体验见证',
      angle: `以一个具体的人在${scenario}的真实使用过程为主线，用细节而非形容词说服`,
      hook: `「${pain}」这件事，我试了三个月才找到答案`,
      sample_headline: `${scenario}的第 30 天，我把它留在了购物车里`,
      rationale: '真实感对抗广告防御心理，适合建立长期品牌信任',
      risk: '需要真实素材支撑，虚构细节会引发反噬',
      fit_score: 88,
    },
    {
      id: 'D2',
      name: '反常识提问',
      angle: '先指出目标人群的普遍误区，再用产品能力给出新解',
      hook: `${pain}，可能不是你不够努力`,
      sample_headline: `关于${brief.product}，我们可能一直都做错了`,
      rationale: '认知缺口带来高完读率，利于专业形象塑造',
      risk: '对论据要求高，论证不充分会被质疑',
      fit_score: 81,
    },
    {
      id: 'D3',
      name: '具体数字锚定',
      angle: `用可验证的时间/数量单位把价值量化，落在「${brief.keywords[0] ?? '核心卖点'}」上`,
      hook: `把「${pain}」压缩到 ${30 + (seed % 40)} 秒能搞定的事`,
      sample_headline: `${brief.product}：把省下的时间还给你`,
      rationale: '量化表达降低理解成本，适合转化导向',
      risk: '数字必须有依据，否则触发事实核查与合规风险',
      fit_score: 84,
    },
  ];

  return {
    big_idea: {
      title: `${brief.brand}·${pick(['把复杂留给我们', '刚刚好的选择', '让每一步都算数'], seed)}`,
      statement: `${proposition}。不是讲我们有多好，而是让${brief.audience}看见自己可以更从容。`,
      rationale: '把品牌主张落在用户自身的状态变化上，而非产品参数上。',
    },
    directions,
    tone_guide: {
      voice: brief.tone,
      dos: ['用第二人称对话', '每段只讲一件事', '先共情再给方案', '具体细节替代形容词'],
      donts: ['不使用绝对化用语', '不承诺效果与收益', '不贬损同类品牌', '不堆砌行业术语'],
      visual_suggestion: '画面以真实使用场景为主，避免过度修图与摆拍感',
    },
    reference_cases: cases.map((item) => ({ name: item.name, why: item.why, source: item.source })),
    recommended_direction: 'D1',
    recommendation_reason: `与「${brief.tone}」调性最一致，且素材门槛可控，${brief.channel}的推荐流环境下完读风险最低`,
    confidence: 0.79,
    risks: ['方向 D3 涉及量化表达，需 A6 核查数据来源后才能使用', '方向 D2 的论点若论据不足可能引发负面评论'],
    evidence: [
      { claim: '具体场景锚定的内容完读率高于泛化表达', source: '行业内容方法论（公开复盘）', reliability: 0.75 },
      { claim: `参考案例「${cases[0]?.name ?? '—'}」的 ${cases[0]?.why ?? ''}`, source: cases[0]?.source ?? '公开案例库', reliability: 0.7 },
    ],
  };
}

/* ------------------------------------------------------------------ */
/* A3 内容策划                                                         */
/* ------------------------------------------------------------------ */

function generatePlan(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const strategy = rec(ctx.strategy);
  const creative = rec(ctx.creative);
  const audience = rec(strategy.audience_profile);
  const house = rec(strategy.message_house);
  const rule = channelRule(brief.channel);
  const pain = strArray(audience.pain_points)[0] ?? '选择困难';
  const shortPain = pain.length > 8 ? pain.slice(0, 8) : pain;
  const benefit = strArray(house.benefits)[0] ?? '更省心';
  const directions = objArray(creative.directions);
  const chosen = directions.find((d) => str(d.id) === str(creative.recommended_direction)) ?? directions[0] ?? {};
  const keyword = brief.keywords[0] ?? brief.product;

  const topics = [
    {
      id: 'T1',
      title: `${pain}？我把${brief.product}用了 30 天`,
      angle: str(chosen.name, '真实体验见证'),
      format: rule.format,
      outline: [
        `钩子：用「${pain}」直接切入，让${brief.audience}第一句就认出自己`,
        `场景：还原${strArray(audience.scenarios)[0] ?? '日常使用'}的具体细节`,
        `方案：${brief.product}如何解决「${pain}」，只讲 3 个点`,
        `证据：实测过程与可核验的素材`,
        `行动：给出低门槛的下一步动作`,
      ],
      cta: `想试试的话，我把链接放在评论区了`,
      estimated_words: 420,
    },
    {
      id: 'T2',
      title: `关于「${keyword}」，先说三个反常识`,
      angle: '反常识提问',
      format: rule.format,
      outline: [
        '结论先行：直接给出与常识相反的观点',
        '论据一：行业普遍做法的成本在哪',
        `论据二：${brief.product}的差异点如何形成`,
        '论据三：可验证的数据或案例',
        '收束：给出一个判断标准而非硬推',
      ],
      cta: '你更认同哪种做法？评论区聊聊',
      estimated_words: 520,
    },
    {
      id: 'T3',
      title: `把「${pain}」压缩掉：${brief.product}的 ${brief.keywords.length || 3} 个关键点`,
      angle: '具体数字锚定',
      format: rule.format,
      outline: [
        '用一组数字给出整体价值',
        '逐条拆解关键点，每点配一个使用场景',
        '对比：不做这件事的成本',
        '信任：来源与依据说明',
        '行动引导',
      ],
      cta: '收藏这篇，下次换购前翻出来看',
      estimated_words: 380,
    },
  ];

  return {
    topics,
    selected_topic: 'T1',
    selection_reason: `与创意方向「${str(chosen.name, '真实体验见证')}」一致，且最贴合${brief.channel}的推荐机制`,
    // 标题控制在 20 字以内，符合小红书信息流截断规则
    headline_candidates: [
      `${shortPain}？我换了${shorten(brief.product, 8)}`,
      `${shorten(brief.product, 8)}用了 30 天`,
      `同事追着问链接的${shorten(brief.product, 8)}`,
      `别再硬扛了，${shortPain}有解`,
      `${(brief.keywords[0] ?? brief.product).slice(0, 10)}值不值？看完再决定`,
    ],
    structure: [
      { section: '开头钩子', goal: '3 秒内建立身份认同', words: 60 },
      { section: '痛点共鸣', goal: `让${brief.audience}确认「说的是我」`, words: 80 },
      { section: '方案展开', goal: `呈现${benefit}`, words: 160 },
      { section: '证据支撑', goal: '降低「是不是智商税」的疑虑', words: 80 },
      { section: '行动引导', goal: '给出明确的下一步', words: 40 },
    ],
    channel_adaptation: [
      { channel: brief.channel, format: rule.format, notes: rule.length_hint },
      { channel: '短视频', format: '分镜脚本', notes: '复用同一选题，改写为 45 秒口播' },
    ],
    publishing_rhythm: [
      { slot: 'T+0', action: `发布${brief.channel}主贴`, note: '选择目标人群活跃时段' },
      { slot: 'T+2天', action: '评论区二次答疑', note: '沉淀高频问题作为下一条选题' },
      { slot: 'T+7天', action: '数据复盘', note: '对比标题 A/B 表现' },
    ],
    keywords: {
      primary: brief.keywords,
      long_tail: [`${keyword}怎么选`, `${brief.product}真实测评`, `${brief.audience}${keyword}`],
      hashtags: `#${brief.brand} #${keyword} #${brief.industry}`,
    },
    confidence: 0.8,
    risks: ['选题 T1 依赖真实使用周期，建议提前准备可核验的素材'],
    evidence: [
      { claim: `${brief.channel}的内容形态为「${rule.format}」`, source: '平台公开规则', reliability: 0.85 },
      { claim: `推荐结构「${rule.blocks.slice(0, 3).join(' / ')}」`, source: '平台运营实践', reliability: 0.75 },
    ],
  };
}

/* ------------------------------------------------------------------ */
/* A4 文案创作                                                         */
/* ------------------------------------------------------------------ */

interface CopyVersion {
  id: string;
  style: string;
  title: string;
  body: string;
  cta: string;
  hashtags: string[];
  word_count: number;
  scenes?: { time: string; visual: string; voiceover: string }[];
}

function buildHashtags(brief: Brief): string[] {
  const keyword = brief.keywords[0] ?? brief.industry;
  return [
    `#${brief.brand}`,
    `#${keyword}`,
    `#${brief.industry}`,
    `#${brief.audience.slice(0, 8)}日常`,
    `#真实使用分享`,
  ].slice(0, 5);
}

/** 渠道化正文骨架：同一策略在不同渠道生成不同形态的内容。 */
function composeBody(brief: Brief, opts: {
  hook: string;
  pain: string;
  scenario: string;
  benefit: string;
  benefits: string[];
  support: string[];
  cta: string;
  styleFlavor: string;
}): string {
  const channel = brief.channel;

  if (channel.includes('抖音')) {
    return [
      `【0-3s 钩子】画面：${opts.scenario}，人物抬头看镜头。口播：「${opts.hook}」`,
      `【3-8s 痛点】画面：特写手忙脚乱的细节。口播：「${opts.pain}，很多人都卡在这一步。」`,
      `【8-25s 方案】画面：使用 ${brief.brand} ${brief.product} 的特写 + 前后对比。口播：「${opts.benefit}。它是怎么做到的？${opts.support[0] ?? ''}」`,
      `【25-40s 证据】画面：真实素材 / 实拍细节。口播：「${opts.support[1] ?? '这是我自己用下来的真实记录'}。」`,
      `【40-50s 转化】画面：${brief.brand} 产品定格 + 字幕。口播：「${opts.cta}」`,
    ].join('\n\n');
  }

  if (channel.includes('公众号')) {
    return [
      opts.hook,
      '',
      `一、为什么「${opts.pain}」这么难解决`,
      `我们在${opts.scenario}这件事上反复试错，本质原因是可选项太多、判断标准太少。`,
      '',
      `二、${brief.brand}的${brief.product}给出的另一种做法`,
      ...opts.benefits.map((item, index) => `${index + 1}. ${item}`),
      '',
      `三、依据与边界`,
      `需要说明的是，${opts.support[2] ?? '以下结论来自我们的实际使用记录'}。它并不适合所有人，如果你属于以下情况，可以再等等。`,
      '',
      opts.cta,
    ].join('\n');
  }

  if (channel.includes('电商')) {
    return [
      `【首屏】${brief.brand}｜${opts.hook}`,
      '',
      `【痛点场景】${opts.pain}。${opts.scenario}时尤其明显。`,
      '',
      '【核心卖点】',
      ...opts.benefits.map((item, index) => `${index + 1}. ${item}`),
      '',
      `【信任背书】${opts.support.join('；')}`,
      '',
      `【售后保障】${brief.constraints[0] ?? '以实际页面承诺为准'}`,
    ].join('\n');
  }

  if (channel.includes('知乎') || channel.includes('PR')) {
    return [
      `先说结论：${opts.hook}`,
      '',
      `论证一：${opts.pain}——这是${brief.audience}最常遇到的卡点。`,
      `论证二：${brief.brand}主张${opts.benefit}。${opts.support[0] ?? ''}`,
      `论证三：${opts.support[1] ?? ''}`,
      '',
      '可能的反驳：有人会认为这套做法成本更高。我们的回答是，评价口径不同结论就不同，本文所述均为可复现的实际观测。',
      '',
      opts.cta,
    ].join('\n');
  }

  // 默认：小红书
  return [
    opts.hook,
    '',
    `作为${opts.scenario}的${brief.audience}，我对${brief.product}的要求其实很简单——别给我添麻烦。`,
    '',
    `但现实是：${opts.pain}。`,
    '',
    `换用${brief.brand}的${brief.product}之后，最直接的感受是：${opts.benefit}。`,
    '',
    '我留下它的三个原因：',
    ...opts.benefits.map((item, index) => `${index + 1}｜${item}`),
    '',
    `${opts.styleFlavor}`,
    '',
    opts.cta,
  ].join('\n');
}

function generateCopy(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const strategy = rec(ctx.strategy);
  const creative = rec(ctx.creative);
  const plan = rec(ctx.plan);
  const audience = rec(strategy.audience_profile);
  const house = rec(strategy.message_house);
  const feedback = strArray(ctx.feedback);
  const isRevision = feedback.length > 0;
  const seed = hash(`${brief.brand}${brief.product}${brief.channel}${isRevision ? 'r' : 'v'}${feedback.length}`);
  const rule = channelRule(brief.channel);

  const pain = strArray(audience.pain_points)[0] ?? '选择成本太高';
  const scenario = strArray(audience.scenarios)[0] ?? '日常使用';
  const benefits = strArray(house.benefits).length ? strArray(house.benefits) : ['更省心', '更稳定', '更少的试错成本'];
  const support = strArray(house.support_points).length ? strArray(house.support_points) : ['基于实际使用记录'];
  const keyword = brief.keywords[0] ?? brief.product;
  const headlines = strArray(plan.headline_candidates);

  // 未经历返工时保留风险表达 —— 用于演示「合规门禁真实拦截」。
  const flavorPool = isRevision
    ? [`说句实在的，它不便宜，但省下的时间对我是划算的。`]
    : [
        `说句实在的，这可能是同类里最好喝的一个选择。`,
        `用下来我觉得这就是${pain}的最优解。`,
        `不夸张地说，这是我今年买得最值的一件。`,
      ];

  const styleDefs = [
    { id: 'V1', style: '主推版·真实体验', title: headlines[0] ?? `${pain}？我换了${brief.product}`, flavor: pick(flavorPool, seed) },
    { id: 'V2', style: '理性版·决策辅助', title: `把「${keyword}」讲清楚：${brief.product}的取舍`, flavor: '我尽量只说能验证的部分，判断留给你。' },
    { id: 'V3', style: '感性版·情绪共鸣', title: `${scenario}的那一刻，我终于不用再纠结了`, flavor: '有些改变不需要理由，舒服就够了。' },
  ];

  const versions: CopyVersion[] = styleDefs.map((style, index) => {
    const cta = index === 2 ? `如果它也戳到你了，点个收藏慢慢看` : `想试试的话，评论区我放了入口`;
    const composed = composeBody(brief, {
      hook: style.title,
      pain,
      scenario,
      benefit: benefits[0] ?? '更省心',
      benefits,
      support,
      cta,
      styleFlavor: style.flavor,
    });
    // 收到事实核查意见后，作者补充来源标注（真实返工中最常见的修改动作）
    const body = isRevision
      ? `${composed}\n\n数据来源：品牌方提供的产品说明与内部实测记录（样本 32 人）`
      : composed;
    const hashtags = buildHashtags(brief);
    const full = `${style.title}\n\n${body}\n\n${hashtags.join(' ')}`;
    return {
      id: style.id,
      style: style.style,
      title: style.title,
      body,
      cta,
      hashtags,
      word_count: full.replace(/\s/g, '').length,
    };
  });

  const claims = [
    {
      text: `${brief.product}面向${brief.audience}，主打「${keyword}」`,
      source: isRevision ? '品牌方提供的产品说明' : '',
    },
    {
      text: `${brief.audience}在${scenario}场景下的主要困扰是「${pain}」`,
      source: isRevision ? `行业洞察库（${brief.industry}）` : '',
    },
    {
      text: `内容形态遵循${brief.channel}「${rule.format}」规范`,
      source: '平台公开规则',
    },
  ];

  return {
    versions,
    recommended_version: 'V1',
    headlines: headlines.length ? headlines : versions.map((v) => v.title),
    claims,
    channel_checklist: rule.blocks.map((block) => ({ block, covered: true })),
    revision_notes: isRevision
      ? feedback.map((item) => ({ from_feedback: item, action: '已按要求调整' }))
      : [],
    confidence: isRevision ? 0.88 : 0.81,
    risks: isRevision ? [] : ['文案中包含主观评价性表述，可能触发合规与事实核查门禁'],
    evidence: isRevision
      ? [{ claim: '所有关键主张均已标注来源', source: '本轮补充', reliability: 0.85 }]
      : [{ claim: `沿用创意方向「${str(rec(creative.big_idea).title, '—')}」`, source: 'A2 创意概念', reliability: 0.8 }],
  };
}

/* ------------------------------------------------------------------ */
/* A5 编辑审校                                                         */
/* ------------------------------------------------------------------ */

function generateEdit(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const draft = rec(ctx.draft);
  const versions = objArray(draft.versions);
  const recommended = str(draft.recommended_version, 'V1');
  const target = versions.find((v) => str(v.id) === recommended) ?? versions[0] ?? {};
  const rule = channelRule(brief.channel);

  const title = str(target.title);
  const body = str(target.body);
  const hashtags = strArray(target.hashtags);
  const plain = `${title}${body}`.replace(/\s/g, '');
  const paragraphs = body.split(/\n+/).filter((p) => p.trim().length > 0);

  /* --- 真实可测量的审校信号 --- */
  const issues: { severity: string; category: string; detail: string; suggestion: string; location: string }[] = [];
  const changeLog: { type: string; detail: string; before: string; after: string }[] = [];

  let structure = 90;
  let clarity = 88;
  let brandVoice = 86;
  let appeal = 84;

  if (title.length > 20 && brief.channel.includes('小红书')) {
    issues.push({
      severity: 'major',
      category: '标题',
      detail: `标题 ${title.length} 字，超出${brief.channel}推荐长度（≤20 字），信息流易被截断`,
      suggestion: '压缩至 20 字以内，保留身份锚点与情绪词',
      location: '标题',
    });
    structure -= 12;
    appeal -= 8;
  }
  if (paragraphs.length < 5) {
    issues.push({
      severity: 'minor',
      category: '结构',
      detail: `段落数偏少（${paragraphs.length} 段），移动端阅读疲劳度上升`,
      suggestion: '按「钩子 / 共鸣 / 方案 / 证据 / 行动」拆分段落',
      location: '正文',
    });
    structure -= 10;
  }
  if (!body.includes(brief.brand)) {
    issues.push({
      severity: 'major',
      category: '品牌',
      detail: '正文未出现品牌名，品牌联想无法建立',
      suggestion: '在方案段自然植入品牌名一次',
      location: '正文',
    });
    brandVoice -= 15;
  }
  if (hashtags.length === 0) {
    issues.push({
      severity: 'minor',
      category: '渠道适配',
      detail: '缺少话题标签，损失搜索与推荐流量',
      suggestion: `补充 ${rule.hashtag_policy}`,
      location: '文末',
    });
    appeal -= 6;
  }
  const longSentence = body.split(/[。！？\n]/).find((s) => s.length > 60);
  if (longSentence) {
    const shortened = `${longSentence.slice(0, 40)}。`;
    changeLog.push({
      type: '长句拆分',
      detail: '将 60 字以上长句拆为短句，提升可读性',
      before: shorten(longSentence, 50),
      after: shortened,
    });
    clarity += 4;
  }
  if (/(嗯|啊|吧|啦)+/.test(body) === false) {
    /* 无口语词则提示补口语，模拟编辑对语气的建议 */
    changeLog.push({
      type: '语气调整',
      detail: `补充口语化连接词，贴合「${brief.tone}」调性`,
      before: '（原句）',
      after: '（加入「其实」「说句实在的」等过渡）',
    });
    brandVoice += 3;
  }

  const revisedBody = [
    longSentence ? body.replace(longSentence, `${longSentence.slice(0, 40)}。`) : body,
  ].join('');

  const revised = {
    title: title.length > 20 && brief.channel.includes('小红书') ? shorten(title, 20) : title,
    body: revisedBody,
    cta: str(target.cta),
    hashtags,
  };

  const score = (value: number) => Math.max(40, Math.min(98, Math.round(value)));
  const scorecard = {
    structure: score(structure),
    clarity: score(clarity),
    brand_voice: score(brandVoice),
    appeal: score(appeal),
  };
  const overall = Math.round(
    (scorecard.structure + scorecard.clarity + scorecard.brand_voice + scorecard.appeal) / 4,
  );

  const blockers = issues.filter((i) => i.severity === 'blocker' || i.severity === 'major');

  return {
    revised,
    change_log: changeLog,
    scorecard: { ...scorecard, overall },
    issues,
    verdict: blockers.length > 0 ? 'revise' : 'pass',
    verdict_reason:
      blockers.length > 0
        ? `存在 ${blockers.length} 项需修订问题（${blockers.map((b) => b.category).join('、')}）`
        : '结构、表达、品牌语气均达到放行标准',
    read_metrics: {
      char_count: plain.length,
      paragraph_count: paragraphs.length,
      avg_sentence_length: Math.round(plain.length / Math.max(1, body.split(/[。！？]/).length)),
    },
    confidence: 0.86,
    risks: [],
    evidence: [{ claim: `按${brief.channel}形态规范「${rule.format}」评审`, source: '平台运营规范', reliability: 0.85 }],
  };
}

/* ------------------------------------------------------------------ */
/* A6 事实核查                                                         */
/* ------------------------------------------------------------------ */

function generateFactCheck(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const revision = Number(ctx.revision ?? 0);
  const draft = rec(ctx.draft);
  const versions = objArray(draft.versions);
  const target = versions.find((v) => str(v.id) === str(draft.recommended_version, 'V1')) ?? versions[0] ?? {};
  const body = `${str(target.title)}\n${str(target.body)}`;

  const claims = objArray(draft.claims);
  const checks: {
    claim: string;
    status: 'verified' | 'unverified' | 'exaggerated' | 'expired' | 'contradicted';
    source: string;
    note: string;
    confidence: number;
  }[] = [];

  for (const claim of claims) {
    const source = str(claim.source);
    checks.push({
      claim: str(claim.text),
      status: source ? 'verified' : 'unverified',
      source: source || '未提供',
      note: source ? '来源可追溯，表述与来源一致' : '主张未标注来源，无法核验，需补充或改为安全表达',
      confidence: source ? 0.85 : 0.6,
    });
  }

  /* 数值型断言检测：数字最容易失实，必须带来源 */
  const numericPatterns: { pattern: RegExp; label: string }[] = [
    { pattern: /\d+(\.\d+)?%/g, label: '百分比数据' },
    { pattern: /\d+\s*倍/g, label: '倍数断言' },
    { pattern: /\d+\s*天/g, label: '时间承诺' },
    { pattern: /\d+\s*人/g, label: '样本量' },
  ];
  const hasSourceMarker = /(来源[:：]|数据来源|据.+统计|实测|样本|参考文献)/.test(body);
  for (const item of numericPatterns) {
    const matched = body.match(item.pattern);
    if (matched && matched.length > 0 && !hasSourceMarker) {
      checks.push({
        claim: `${item.label}「${matched.slice(0, 2).join('、')}」`,
        status: 'unverified',
        source: '未提供',
        note: '正文出现量化表述但未见来源标注，属于高风险项',
        confidence: 0.55,
      });
    }
  }

  /* 绝对化/效果承诺类断言 → 交由合规智能体处理，但事实侧同步标记 */
  const absolutePatterns = ['最好', '最优', '最值', '第一', '唯一', '绝对', '保证', '永久'];
  for (const word of absolutePatterns) {
    if (body.includes(word)) {
      checks.push({
        claim: `主观绝对化表述「${word}」`,
        status: 'exaggerated',
        source: '—',
        note: '无法被证实的主观断言，建议改为可验证的限定描述',
        confidence: 0.7,
      });
      break;
    }
  }

  const unverified = checks.filter((c) => c.status === 'unverified');
  const exaggerated = checks.filter((c) => c.status === 'exaggerated');
  const blockers = unverified.length + exaggerated.length;
  const risk_level = blockers >= 2 ? 'high' : blockers === 1 ? 'medium' : 'low';

  return {
    checks,
    risk_level,
    verdict: blockers > 0 ? 'revise' : 'pass',
    verdict_reason:
      blockers > 0
        ? `发现 ${unverified.length} 项无来源主张、${exaggerated.length} 项主观绝对化表述`
        : '所有关键主张均可追溯来源，未发现夸大或矛盾信息',
    required_fixes: unverified
      .map((c) => `为「${shorten(c.claim, 30)}」补充可核验来源，或删除该表述`)
      .concat(exaggerated.map((c) => `将「${shorten(c.claim, 30)}」改为可验证的限定描述`)),
    safe_rewrites: exaggerated.map((c) => ({
      original: c.claim,
      rewrite: '改为「基于我们实际使用与公开资料的描述」，并去掉绝对化限定',
      reason: '避免无法证实的最高级表述',
    })),
    verification_scope: [
      `核查对象：${brief.channel} 主推版本（${str(target.id, 'V1')}）`,
      `核查项：${checks.length} 项`,
      '核查方式：来源标注检查 + 数值断言扫描 + 绝对化表述识别',
    ],
    confidence: 0.84,
    risks:
      risk_level === 'high'
        ? ['存在多项无来源量化表述，若直接发布存在误导风险，已行使否决权']
        : [],
    needs_human_review: risk_level === 'high' && revision > 0,
    evidence: checks
      .filter((c) => c.status === 'verified')
      .map((c) => ({ claim: c.claim, source: c.source, reliability: 0.85 })),
  };
}

/* ------------------------------------------------------------------ */
/* A10 效果预估与复盘                                                  */
/* ------------------------------------------------------------------ */

function generateAnalysis(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const strategy = rec(ctx.strategy);
  const audience = rec(strategy.audience_profile);
  const objectives = objArray(strategy.objectives);
  const seed = hash(`${brief.brand}${brief.channel}${brief.objective}`);
  const rule = channelRule(brief.channel);
  const objective = str(objectives[0]?.type, brief.objective);

  const band = (mid: number, spread: number) => ({
    low: Math.round(mid - spread),
    mid: Math.round(mid),
    high: Math.round(mid + spread),
    unit: '',
  });

  return {
    mode: 'pre_publish_estimate',
    predicted: {
      exposure: { ...band(10_000 + (seed % 8) * 5_000, 6_000), unit: '次' },
      ctr: { ...band(4 + (seed % 4), 1.5), unit: '%' },
      engagement: { ...band(3 + (seed % 3), 1.2), unit: '%' },
      conversion: { ...band(1 + (seed % 2), 0.6), unit: '%' },
      basis: `参照账号历史同形态内容（${rule.format}）的区间表现，非承诺值`,
    },
    objective_alignment: {
      objective,
      score: 78 + (seed % 15),
      note: `内容结构与「${objective}」目标一致，${brief.channel}形态匹配`,
    },
    attribution: [
      { factor: '标题钩子强度', impact: 'high', note: '标题决定 70% 以上的首屏点击决策' },
      { factor: '开头 3 秒信息密度', impact: 'high', note: '与完读率强相关' },
      { factor: '话题标签覆盖', impact: 'medium', note: `建议按「${rule.hashtag_policy}」布局` },
      { factor: '发布时段', impact: 'medium', note: `对齐${brief.audience}活跃高峰` },
    ],
    optimizations: [
      { priority: 'high', action: '标题做 A/B 测试，保留 2 个方向各投放 24 小时', expected_gain: 'CTR +15%~30%', effort: '低' },
      { priority: 'high', action: '评论区预置高频疑问的官方回答，提升正向评论占比', expected_gain: '互动率 +10%', effort: '低' },
      { priority: 'medium', action: '把长文二次剪辑为 45 秒短视频，复用同一选题', expected_gain: '触达人群 +40%', effort: '中' },
      { priority: 'low', action: '沉淀本次可复用素材至品牌资产库', expected_gain: '下次制作成本 -30%', effort: '低' },
    ],
    ab_tests: [
      { hypothesis: '身份锚点型标题优于痛点型标题', variant_a: '早八人救命！办公室 30 秒喝上冷萃', variant_b: '起不来又想喝好的？我找到办法了', metric: 'CTR' },
      { hypothesis: '带具体数字的封面提升收藏率', variant_a: '封面含「30 秒」数字', variant_b: '封面纯场景图', metric: '收藏率' },
    ],
    next_brief_suggestions: [
      `将评论区高频问题沉淀为下一条选题（面向${brief.audience}）`,
      `补充「${strArray(audience.pain_points)[1] ?? '次级痛点'}」相关的内容测试`,
      '若首条表现超预期，48 小时内追加同方向内容形成话题聚合',
    ],
    cautions: ['以上为区间预估，不构成效果承诺；实际表现受平台流量分配影响显著'],
    confidence: 0.66,
    risks: ['预估基于历史区间数据，样本有限', '平台算法调整可能显著影响实际曝光'],
    evidence: [
      { claim: `内容形态为「${rule.format}」`, source: '平台公开规则', reliability: 0.85 },
      { claim: '标题对首屏点击影响最大', source: '内容运营通用共识', reliability: 0.7 },
    ],
  };
}

/* ------------------------------------------------------------------ */
/* A7 品牌合规                                                         */
/* ------------------------------------------------------------------ */

function generateCompliance(ctx: Record<string, unknown>): unknown {
  const brief = asBrief(ctx);
  const revision = Number(ctx.revision ?? 0);
  const draft = rec(ctx.draft);
  const versions = objArray(draft.versions);
  const target = versions.find((v) => str(v.id) === str(draft.recommended_version, 'V1')) ?? versions[0] ?? {};
  const title = str(target.title);
  const body = str(target.body);
  const hashtags = strArray(target.hashtags).join(' ');
  const fullText = `${title}\n${body}\n${hashtags}`;

  /* --- 真实词库扫描 + 品牌语气检查 --- */
  const scan = scanCompliance(fullText, brief.industry);
  const voice = checkBrandVoice(fullText, brief.tone);
  const rewrite = autoRewrite(fullText, scan.hits);

  const blockers = scan.hits.filter((h) => h.severity === 'blocker');
  const majors = scan.hits.filter((h) => h.severity === 'major');
  const minors = scan.hits.filter((h) => h.severity === 'minor');

  const items = [
    ...scan.hits.map((hit) => ({
      severity: hit.severity,
      category: hit.category,
      term: hit.term,
      detail: `${hit.term} —— ${hit.snippet}`,
      suggestion: hit.fix,
      law: hit.law,
    })),
    ...scan.required_failures.map((fail) => ({
      severity: fail.severity,
      category: fail.category,
      term: '',
      detail: fail.hint,
      suggestion: fail.fix,
      law: fail.law,
    })),
    ...voice.issues.map((issue) => ({
      severity: issue.severity,
      category: issue.type,
      term: '',
      detail: issue.detail,
      suggestion: issue.suggestion,
      law: '—',
    })),
  ];

  const penalty = blockers.length * 25 + majors.length * 10 + minors.length * 4 + scan.required_failures.length * 8;
  const score = Math.max(0, 100 - penalty);
  const risk_level = scan.risk_level;
  const verdict: 'pass' | 'revise' | 'reject' =
    blockers.length > 0 || scan.risk_level === 'high'
      ? 'revise'
      : majors.length > 0 || scan.required_failures.length > 0 || voice.score < 70
        ? 'revise'
        : 'pass';

  const required_fixes = [
    ...blockers.map((h) => `【必改】删除或替换「${h.term}」（${h.category}）：${h.fix}`),
    ...majors.map((h) => `【强烈建议】「${h.term}」（${h.category}）：${h.fix}`),
    ...scan.required_failures.map((f) => `【必备要素缺失】${f.hint}`),
    ...minors.slice(0, 2).map((h) => `【建议】「${h.term}」：${h.fix}`),
  ];

  return {
    hits: items,
    summary: {
      blocker: blockers.length,
      major: majors.length,
      minor: minors.length,
      missing_required: scan.required_failures.length,
    },
    brand_consistency: {
      score: Math.round((voice.score / 20) * 10) / 10,
      issues: voice.issues.map((i) => ({ type: i.type, detail: i.detail, suggestion: i.suggestion })),
      terminology_ok: true,
      disclaimer_ok: !scan.required_failures.some((f) => f.category.includes('风险提示')),
    },
    risk_level,
    verdict,
    verdict_reason:
      verdict === 'pass'
        ? '未发现违反广告法与品牌规范的表述，可进入发布审批'
        : `命中 ${blockers.length} 项阻断项、${majors.length} 项重要项，禁止直接发布`,
    required_fixes,
    safe_rewrites: rewrite.applied,
    auto_fixed_text: rewrite.text,
    checked_against: [
      '《广告法》第九条（绝对化用语）',
      '《广告法》第十七条（医疗功效）',
      '《广告法》第二十五条（收益承诺）',
      ...(brief.industry ? [`行业专项规则：${brief.industry}`] : []),
      `品牌调性：${brief.tone}`,
    ],
    compliance_score: score,
    confidence: 0.92,
    needs_human_review: risk_level === 'high' && revision > 0,
    risks: blockers.length > 0 ? [`存在阻断级合规风险：${blockers.map((b) => b.term).join('、')}`] : [],
    evidence: scan.hits.slice(0, 5).map((h) => ({
      claim: `「${h.term}」违反${h.category}`,
      source: h.law,
      reliability: 0.95,
    })),
  };
}

/* ------------------------------------------------------------------ */
/* 生成器注册表                                                        */
/* ------------------------------------------------------------------ */

const GENERATORS: Record<string, (ctx: Record<string, unknown>) => unknown> = {
  'A1.strategy': generateStrategy,
  'A2.creative': generateCreative,
  'A3.plan': generatePlan,
  'A4.copy': generateCopy,
  'A5.edit': generateEdit,
  'A6.factcheck': generateFactCheck,
  'A7.compliance': generateCompliance,
  'A10.analyze': generateAnalysis,
};

/* ------------------------------------------------------------------ */
/* Provider                                                            */
/* ------------------------------------------------------------------ */

const LATENCY_MIN = 220;
const LATENCY_MAX = 520;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class MockProvider implements LLMProvider {
  readonly name = 'mock';
  readonly model = 'mock-creator-engine-v1';
  readonly simulated = true;

  async chat(request: LLMRequest): Promise<LLMResponse> {
    const started = Date.now();
    const context = request.context ?? {};
    const generator = GENERATORS[request.purpose];

    if (!generator) {
      throw new Error(`Mock 引擎未实现用途：${request.purpose}`);
    }

    await sleep(LATENCY_MIN + Math.random() * (LATENCY_MAX - LATENCY_MIN));

    const payload = generator(context);
    const content = JSON.stringify(payload, null, 2);
    const promptText = request.messages.map((m) => m.content).join('\n');

    return {
      content,
      provider: this.name,
      model: this.model,
      usage: {
        prompt_tokens: estimateTokens(promptText),
        completion_tokens: estimateTokens(content),
      },
      latency_ms: Date.now() - started,
      simulated: true,
    };
  }
}
