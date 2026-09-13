/**
 * 合规词库与规则引擎（服务于 A7 品牌合规智能体）。
 *
 * 说明：MVP 使用本地词表 + 规则匹配，保证离线可跑、结果可解释、误杀可追溯。
 * 生产环境应升级为「词库服务 + 法规库 RAG + 人工误杀反馈学习」。
 */

export type Severity = 'blocker' | 'major' | 'minor';

export interface LexiconHit {
  term: string;
  category: string;
  severity: Severity;
  law: string;
  snippet: string;
  fix: string;
}

interface LexiconGroup {
  category: string;
  severity: Severity;
  law: string;
  fix: string;
  terms: string[];
}

/* ------------------------------------------------------------------ */
/* 通用词库                                                            */
/* ------------------------------------------------------------------ */

export const LEXICON_GROUPS: LexiconGroup[] = [
  {
    category: '绝对化用语',
    severity: 'blocker',
    law: '《广告法》第九条第（三）项：不得使用“国家级”“最高级”“最佳”等用语',
    fix: '删除绝对化表述，改为可验证的限定描述，如「同类产品中较优」「我们实测中表现更好」。',
    terms: [
      '国家级', '世界级', '最高级', '最佳', '最好', '最强', '最大', '最优', '最先进',
      '最高', '最值', '最省', '最专业', '最靠谱', '最健康', '最好用', '最好吃', '最舒适',
      '最便宜', '最划算', '最低价', '最高端', '第一品牌', '全国第一', '全球第一',
      '销量第一', '排名第一', '行业第一', '首家', '首个', '首创', '独家', '唯一',
      '顶级', '顶尖', '极致', '至尊', '王牌', '领导品牌', '行业领先', '国际领先',
      '绝对', '100%', '百分百', '全网最低', '史上最低', '永久', '终身', '万能',
      '无所不能', '无与伦比', '空前绝后', '登峰造极', '绝无仅有', '完美',
    ],
  },
  {
    category: '医疗功效宣称',
    severity: 'blocker',
    law: '《广告法》第十七条：非医疗、药品、医疗器械广告不得涉及疾病治疗功能',
    fix: '删除疾病治疗相关表述，仅描述使用体验与外观感受，如「用后感觉更清爽」。',
    terms: [
      '治疗', '治愈', '根治', '疗效', '药效', '包治', '主治', '消炎', '杀菌',
      '抗癌', '抗肿瘤', '降血压', '降血糖', '降血脂', '降三高', '减肥', '丰胸',
      '壮阳', '排毒', '无副作用', '药到病除', '立竿见影', '立刻见效', '当天见效',
      '一周见效', '七天见效', '提高免疫力', '修复受损', '医用级',
    ],
  },
  {
    category: '金融收益承诺',
    severity: 'blocker',
    law: '《广告法》第二十五条：不得对未来效果、收益作保证性承诺',
    fix: '删除保本/保收益表述，补充「市场有风险，投资需谨慎」。',
    terms: [
      '保本', '保收益', '保本保息', '稳赚', '稳赚不赔', '无风险', '零风险',
      '高收益', '高回报', '年化收益', '躺赚', '一夜暴富', '必赚', '稳赢', '收益翻倍',
    ],
  },
  {
    category: '效果与时限承诺',
    severity: 'major',
    law: '《广告法》第二十八条：不得对商品性能、功能作虚假或引人误解的宣传',
    fix: '删除保证性承诺，改为条件化的客观描述，如「在 XX 条件下，实测数据为 XX」。',
    terms: ['保证有效', '保证通过', '保过', '包过', '无效退款', '立刻变白', '马上见效', '一次性解决'],
  },
  {
    category: '竞品贬损',
    severity: 'major',
    law: '《反不正当竞争法》第十一条：不得编造、传播虚假信息损害竞争对手商誉',
    fix: '删除贬损性对比，改为客观参数对比并标注测试条件与来源。',
    terms: ['最差', '垃圾品牌', '假货', '山寨货', '吊打同行', '碾压所有'],
  },
  {
    category: '版权与来源风险',
    severity: 'minor',
    law: '《著作权法》：使用他人作品需获得授权',
    fix: '补充图片来源与授权说明，避免使用「图片来源网络」等无授权表述。',
    terms: ['图片来源网络', '转载无需授权', '素材随便用', '网图侵删'],
  },
  {
    category: '个人信息风险',
    severity: 'minor',
    law: '《个人信息保护法》：处理个人信息需明示目的并取得同意',
    fix: '删除对个人信息的直接索取表述，改为引导至合规表单并说明用途。',
    terms: ['加微信领取', '私信发身份证', '填写手机号即可', '详细住址'],
  },
];

/* ------------------------------------------------------------------ */
/* 行业附加规则                                                        */
/* ------------------------------------------------------------------ */

export interface IndustryRule {
  category: string;
  severity: Severity;
  law: string;
  fix: string;
  terms: string[];
  /** 该行业文案必须包含的内容（缺失则告警） */
  required?: { pattern: RegExp; hint: string; severity: Severity };
}

export const INDUSTRY_RULES: Record<string, IndustryRule[]> = {
  医疗健康: [
    {
      category: '医疗宣传资质',
      severity: 'major',
      law: '《医疗广告管理办法》第七条：医疗广告须经审查并标注审查文号',
      fix: '补充机构资质与审查文号，或删除诊疗相关表述改为科普口吻。',
      terms: ['专家推荐', '三甲医院指定', '临床验证', '有效率99%'],
      required: {
        pattern: /(指南|临床|研究|文献|来源|参考文献)/,
        hint: '健康类内容涉及结论时需标注权威来源（临床研究 / 诊疗指南）',
        severity: 'major',
      },
    },
  ],
  金融: [
    {
      category: '金融风险提示缺失',
      severity: 'major',
      law: '《广告法》第二十五条：金融产品广告应显著提示风险',
      fix: '在文末补充「市场有风险，投资需谨慎」等风险提示。',
      terms: ['稳赚', '保收益'],
      required: {
        pattern: /(风险提示|投资需谨慎|市场有风险|不构成投资建议)/,
        hint: '金融类内容必须包含风险提示',
        severity: 'major',
      },
    },
  ],
  教育培训: [
    {
      category: '教育培训承诺',
      severity: 'blocker',
      law: '《广告法》第二十四条：教育、培训广告不得对升学、通过考试作保证性承诺',
      fix: '删除提分/保过承诺，改为描述教学方法与学习过程。',
      terms: ['保过', '保分', '提分保证', '不过退款', '一次通过'],
      required: {
        pattern: /(课程|课时|教学|讲师|师资|大纲)/,
        hint: '教育类内容建议说明课程与师资信息',
        severity: 'minor',
      },
    },
  ],
  食品饮料: [
    {
      category: '食品功效宣称',
      severity: 'major',
      law: '《食品安全法》第七十三条：食品广告不得涉及疾病预防、治疗功能',
      fix: '删除保健功效表述，仅描述口感、配料与食用场景。',
      terms: ['防癌', '抗癌', '治疗便秘', '降三高', '保健功效'],
    },
  ],
  美妆个护: [
    {
      category: '化妆品医疗术语',
      severity: 'major',
      law: '《化妆品监督管理条例》第二十二条：不得明示或暗示具有医疗作用',
      fix: '将医疗术语替换为化妆品功效用语，如用「舒缓」替代「消炎」。',
      terms: ['消炎', '抗炎', '修复损伤', '再生', '医用面膜'],
    },
  ],
  企业服务: [
    {
      category: 'B2B 效果承诺',
      severity: 'major',
      law: '《广告法》第二十八条：不得对服务效果作保证性承诺',
      fix: '将效果承诺改为可验证的客户案例数据，并标注统计口径。',
      terms: ['必定降本50%', '保证翻倍', '零风险上线'],
    },
  ],
};

/* ------------------------------------------------------------------ */
/* 扫描器                                                              */
/* ------------------------------------------------------------------ */

function snippetOf(text: string, index: number, length: number): string {
  const start = Math.max(0, index - 12);
  const end = Math.min(text.length, index + length + 12);
  const prefix = start > 0 ? '…' : '';
  const suffix = end < text.length ? '…' : '';
  return `${prefix}${text.slice(start, end)}${suffix}`;
}

export interface RequiredFailure {
  category: string;
  hint: string;
  severity: Severity;
  law: string;
  fix: string;
}

export interface ScanResult {
  hits: LexiconHit[];
  required_failures: RequiredFailure[];
  risk_level: 'low' | 'medium' | 'high';
}

/** 多轮扫描：通用词库 → 行业专项 → 必备要素检查。 */
export function scanCompliance(text: string, industry: string): ScanResult {
  const hits: LexiconHit[] = [];
  const seen = new Set<string>();

  const pushHit = (
    group: { category: string; severity: Severity; law: string; fix: string },
    term: string,
    index: number,
  ): void => {
    const key = `${group.category}::${term}`;
    if (seen.has(key)) return;
    seen.add(key);
    hits.push({
      term,
      category: group.category,
      severity: group.severity,
      law: group.law,
      snippet: snippetOf(text, index, term.length),
      fix: group.fix,
    });
  };

  for (const group of LEXICON_GROUPS) {
    for (const term of group.terms) {
      const index = text.indexOf(term);
      if (index >= 0) pushHit(group, term, index);
    }
  }

  const industryRules = INDUSTRY_RULES[industry] ?? [];
  for (const rule of industryRules) {
    for (const term of rule.terms) {
      const index = text.indexOf(term);
      if (index >= 0) pushHit(rule, term, index);
    }
  }

  const required_failures: RequiredFailure[] = [];
  for (const rule of industryRules) {
    if (!rule.required) continue;
    if (!rule.required.pattern.test(text)) {
      required_failures.push({
        category: rule.category,
        hint: rule.required.hint,
        severity: rule.required.severity,
        law: rule.law,
        fix: rule.fix,
      });
    }
  }

  const blockers = hits.filter((h) => h.severity === 'blocker').length;
  const majors = hits.filter((h) => h.severity === 'major').length;
  const risk_level: ScanResult['risk_level'] =
    blockers > 0 || required_failures.some((f) => f.severity === 'blocker')
      ? 'high'
      : majors > 0 || required_failures.length > 0
        ? 'medium'
        : 'low';

  return { hits, required_failures, risk_level };
}

/**
 * 自动改写：把命中的违规词替换为合规表达。
 * 返回改写后的文本与替换清单，供 A4 返工时直接参考。
 */
export const SAFE_REPLACEMENTS: Record<string, string> = {
  最好: '表现不错',
  最佳: '较优',
  最强: '偏强',
  最大: '较大',
  最优: '更优',
  最先进: '较为先进',
  最便宜: '价格更有优势',
  最划算: '性价比不错',
  最低价: '活动价',
  最高端: '偏高端',
  第一品牌: '较早进入该领域的品牌',
  全国第一: '在全国范围内表现突出',
  全球第一: '在全球范围内表现突出',
  销量第一: '销量表现突出',
  排名第一: '排名靠前',
  首家: '较早推出',
  首个: '较早推出',
  独家: '自有',
  唯一: '少数具备该能力的',
  顶级: '高规格',
  顶尖: '高水准',
  极致: '很讲究',
  绝对: '相对',
  '100%': '绝大多数情况下',
  永久: '长期',
  终身: '长期',
  万能: '多场景适用',
  完美: '很满意',
  治疗: '护理',
  治愈: '改善',
  根治: '缓解',
  疗效: '使用感受',
  消炎: '舒缓',
  杀菌: '清洁',
  减肥: '身材管理',
  排毒: '代谢支持',
  无副作用: '成分温和',
  立竿见影: '用后感受明显',
  立刻见效: '短期内可感知变化',
  提高免疫力: '状态支持',
  保本: '相对稳健',
  保收益: '历史表现',
  稳赚: '收益存在波动',
  无风险: '风险相对可控',
  高收益: '收益与风险并存',
  高回报: '回报存在不确定性',
};

export function autoRewrite(text: string, hits: LexiconHit[]): { text: string; applied: { from: string; to: string }[] } {
  let output = text;
  const applied: { from: string; to: string }[] = [];
  for (const hit of hits) {
    const replacement = SAFE_REPLACEMENTS[hit.term];
    if (!replacement) continue;
    if (!output.includes(hit.term)) continue;
    output = output.split(hit.term).join(replacement);
    applied.push({ from: hit.term, to: replacement });
  }
  return { text: output, applied };
}

/* ------------------------------------------------------------------ */
/* 品牌一致性检查                                                      */
/* ------------------------------------------------------------------ */

export interface BrandVoiceIssue {
  type: string;
  detail: string;
  suggestion: string;
  severity: Severity;
}

/** 按品牌调性关键词检查文案语气是否走偏。 */
export function checkBrandVoice(text: string, tone: string): { score: number; issues: BrandVoiceIssue[] } {
  const issues: BrandVoiceIssue[] = [];
  let score = 100;

  const rigid = ['综上所述', '特此通知', '本公司', '兹定于', '务必', '敬请知悉'];
  const casual = ['绝绝子', 'yyds', '栓Q', '家人们', '宝子', '爆改'];
  const hyped = ['疯狂', '史上', '震惊', '不看后悔', '速抢'];

  for (const word of rigid) {
    if (text.includes(word) && /(轻松|真实|年轻|活泼|幽默|温暖)/.test(tone)) {
      issues.push({
        type: '语气偏正式',
        detail: `出现书面化表达「${word}」，与设定的「${tone}」调性不符`,
        suggestion: '改写为口语化表达，或拆成短句。',
        severity: 'minor',
      });
      score -= 6;
    }
  }

  for (const word of casual) {
    if (text.includes(word) && !/(年轻|活泼|网感|幽默)/.test(tone)) {
      issues.push({
        type: '网络用语过度',
        detail: `「${word}」与设定的「${tone}」调性不匹配`,
        suggestion: '替换为更中性的口语表达。',
        severity: 'minor',
      });
      score -= 6;
    }
  }

  for (const word of hyped) {
    if (text.includes(word)) {
      issues.push({
        type: '标题党倾向',
        detail: `出现煽动性表达「${word}」`,
        suggestion: '改为具体事实描述，避免空泛夸张。',
        severity: 'minor',
      });
      score -= 4;
    }
  }

  return { score: Math.max(40, Math.min(100, score)), issues };
}
