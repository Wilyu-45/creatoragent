import { useState } from 'react';
import type { AgentsResponse, KnowledgeView, PublicConfigView } from '../lib/api.ts';
import { getApiToken, setApiToken } from '../lib/api.ts';
import { GoldenPanel } from './GoldenPanel.tsx';
import { KnowledgePanel } from './KnowledgePanel.tsx';
import { MemoryPanel } from './MemoryPanel.tsx';
import { Chip, Spinner } from './ui.tsx';

type Tab = 'runtime' | 'knowledge' | 'memory' | 'golden';

export function SettingsDrawer({
  config,
  knowledge,
  agents,
  saving,
  onClose,
  onSave,
  onToast,
}: {
  config: PublicConfigView;
  knowledge: KnowledgeView | null;
  /** 已实现的智能体清单（驱动「智能体模型覆盖」的行列表）；未加载时回落到 config 既有键 */
  agents?: AgentsResponse | null;
  saving: boolean;
  onClose: () => void;
  onSave: (patch: Record<string, unknown>) => void;
  onToast?: (text: string, error?: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>('runtime');
  const [provider, setProvider] = useState(config.llm.provider);
  const [model, setModel] = useState(config.llm.model);
  const [baseUrl, setBaseUrl] = useState(config.llm.baseUrl);
  const [apiKey, setApiKey] = useState('');
  const [temperature, setTemperature] = useState(config.llm.temperature);
  const [maxTokens, setMaxTokens] = useState(config.llm.maxTokens);
  const [timeoutMs, setTimeoutMs] = useState(config.llm.timeoutMs);
  const [turnBudget, setTurnBudget] = useState(config.turnBudget);
  const [maxRevisions, setMaxRevisions] = useState(config.maxRevisions);
  const [qualityThreshold, setQualityThreshold] = useState(config.qualityThreshold);
  const [autoApprove, setAutoApprove] = useState(config.autoApprove);
  const [costBudgetUsd, setCostBudgetUsd] = useState(config.costBudgetUsd);
  const [tokenBudget, setTokenBudget] = useState(config.tokenBudget);
  const [llmCache, setLlmCache] = useState(config.llmCache);
  const [embeddingProvider, setEmbeddingProvider] = useState(config.embedding.provider);
  const [embeddingModel, setEmbeddingModel] = useState(config.embedding.model);
  const [embeddingWeight, setEmbeddingWeight] = useState(config.embedding.weight);
  const [embeddingApiKey, setEmbeddingApiKey] = useState('');
  const [publishWebhookUrl, setPublishWebhookUrl] = useState(config.publish.webhookUrl);
  const [publishAutoDispatch, setPublishAutoDispatch] = useState(config.publish.autoDispatch);
  const [publishRetry, setPublishRetry] = useState(config.publish.retry);
  const [judgeMode, setJudgeMode] = useState(config.judge.mode);
  const [judgeProvider, setJudgeProvider] = useState(config.judge.provider);
  const [judgePassThreshold, setJudgePassThreshold] = useState(config.judge.passThreshold);
  const [judgeWeight, setJudgeWeight] = useState(config.judge.weight);
  const [apiToken, setApiTokenState] = useState(getApiToken());
  const [siteUrlsText, setSiteUrlsText] = useState(config.search.siteUrls.join('\n'));
  // 每智能体模型覆盖的本地编辑态：apiKey 永不回显，初始恒为空 = 保持不变
  const [agentModels, setAgentModels] = useState<Record<string, { model: string; baseUrl: string; apiKey: string }>>(
    () =>
      Object.fromEntries(
        Object.entries(config.llm.agentModels).map(([id, ov]) => [
          id,
          { model: ov.model, baseUrl: ov.baseUrl, apiKey: '' },
        ]),
      ),
  );
  // 行列表 = 已实现智能体 ∪ config 里已有的覆盖键（agents 未加载时仍能看到既有覆盖）
  const agentRows: { id: string; name: string }[] = [
    ...(agents?.implemented ?? []).map((a) => ({ id: a.id, name: a.name })),
    ...Object.keys(config.llm.agentModels)
      .filter((id) => !(agents?.implemented ?? []).some((a) => a.id === id))
      .map((id) => ({ id, name: '自定义覆盖' })),
  ];

  const setAgentField = (id: string, field: 'model' | 'baseUrl' | 'apiKey', value: string): void => {
    setAgentModels((prev) => ({
      ...prev,
      [id]: { ...(prev[id] ?? { model: '', baseUrl: '', apiKey: '' }), [field]: value },
    }));
  };

  const save = (): void => {
    const patch: Record<string, unknown> = {
      provider,
      model,
      baseUrl,
      temperature,
      maxTokens,
      timeoutMs,
      turnBudget,
      maxRevisions,
      qualityThreshold,
      autoApprove,
      costBudgetUsd,
      tokenBudget,
      llmCache,
      embeddingProvider,
      embeddingModel,
      embeddingWeight,
      publishWebhookUrl,
      publishAutoDispatch,
      publishRetry,
      judgeMode,
      judgeProvider,
      judgePassThreshold,
      judgeWeight,
      siteUrls: siteUrlsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      // 每智能体覆盖：apiKey 仅输入非空时携带（缺失 = 保留已存值，防止误清）；
      // 三字段全空的条目由服务端删除该覆盖
      agentModels: Object.fromEntries(
        Object.entries(agentModels).map(([id, ov]) => {
          const entry: { model?: string; baseUrl?: string; apiKey?: string } = {};
          if (ov.model.trim()) entry.model = ov.model.trim();
          if (ov.baseUrl.trim()) entry.baseUrl = ov.baseUrl.trim();
          if (ov.apiKey.trim()) entry.apiKey = ov.apiKey.trim();
          return [id, entry];
        }),
      ),
    };
    if (apiKey.trim()) patch.apiKey = apiKey.trim();
    if (embeddingApiKey.trim()) patch.embeddingApiKey = embeddingApiKey.trim();
    onSave(patch);
  };

  /** Token 只保存在浏览器本地（后端从请求头读取），因此不走配置保存接口。 */
  const saveToken = (): void => {
    setApiToken(apiToken.trim());
    onClose();
  };

  return (
    <div className="drawer-mask" onClick={onClose}>
      <div className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h3>运行时设置</h3>
            <div className="muted small">修改即时生效，无需重启服务</div>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="tabs" style={{ marginTop: 14 }}>
          <button className={`tab${tab === 'runtime' ? ' active' : ''}`} onClick={() => setTab('runtime')}>
            运行配置
          </button>
          <button className={`tab${tab === 'knowledge' ? ' active' : ''}`} onClick={() => setTab('knowledge')}>
            知识库
          </button>
          <button className={`tab${tab === 'memory' ? ' active' : ''}`} onClick={() => setTab('memory')}>
            记忆库
          </button>
          <button className={`tab${tab === 'golden' ? ' active' : ''}`} onClick={() => setTab('golden')}>
            回归测试
          </button>
        </div>

        {tab === 'runtime' ? (
          <>
            <div className="field">
              <label>模型提供方</label>
              <select value={provider} onChange={(e) => setProvider(e.target.value as PublicConfigView['llm']['provider'])}>
                <option value="mock">mock（内置离线引擎，无需密钥）</option>
                <option value="openai">openai（兼容 OpenAI 协议的任意网关）</option>
              </select>
            </div>

            <div className="form-grid" style={{ marginTop: 12 }}>
              <div className="field">
                <label>模型名</label>
                <input value={model} onChange={(e) => setModel(e.target.value)} />
              </div>
              <div className="field">
                <label>Base URL</label>
                <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
              </div>
            </div>

            <div className="row small" style={{ gap: 6, flexWrap: 'wrap', marginTop: 8, alignItems: 'center' }}>
              <span className="muted">本地模型（OpenAI 兼容端点）：</span>
              {(
                [
                  ['Ollama', 'http://localhost:11434/v1'],
                  ['LM Studio', 'http://localhost:1234/v1'],
                  ['vLLM', 'http://localhost:8000/v1'],
                ] as const
              ).map(([label, url]) => (
                <button key={label} type="button" className="btn btn-sm btn-ghost" onClick={() => setBaseUrl(url)}>
                  {label}
                </button>
              ))}
              <span className="muted">
                本机 / 私网端点不计费（token 照记）；本地服务需暴露 OpenAI 兼容接口，多数文本模型无需 API Key。
              </span>
            </div>

            <div className="field" style={{ marginTop: 12 }}>
              <label>
                API Key{' '}
                {config.llm.apiKeySet ? (
                  <Chip tone="tone-ok" mono>
                    已设置 {config.llm.apiKeyMasked}
                  </Chip>
                ) : (
                  <Chip tone="tone-warn">未设置</Chip>
                )}
              </label>
              <input
                type="password"
                value={apiKey}
                placeholder="留空表示不修改"
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>

            <div className="form-grid" style={{ marginTop: 12 }}>
              <div className="field">
                <label>Temperature</label>
                <input
                  type="number"
                  step="0.1"
                  value={temperature}
                  onChange={(e) => setTemperature(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>Max Tokens</label>
                <input type="number" value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={timeoutMs} onChange={(e) => setTimeoutMs(Number(e.target.value))} />
              </div>
            </div>

            <div className="section-h">智能体模型覆盖（可选）</div>
            {agentRows.map(({ id, name }) => {
              const ov = agentModels[id] ?? { model: '', baseUrl: '', apiKey: '' };
              const info = config.llm.agentModels[id];
              return (
                <div className="form-grid" key={id} style={{ marginBottom: 8 }}>
                  <div className="field">
                    <label>
                      {id} · {name}{' '}
                      {info?.apiKeySet ? (
                        <Chip tone="tone-ok" mono>
                          密钥已设置 {info.apiKeyMasked}
                        </Chip>
                      ) : null}
                    </label>
                    <input
                      placeholder="继承全局模型"
                      value={ov.model}
                      onChange={(e) => setAgentField(id, 'model', e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label>Base URL</label>
                    <input
                      placeholder="同全局端点"
                      value={ov.baseUrl}
                      onChange={(e) => setAgentField(id, 'baseUrl', e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label>API Key</label>
                    <input
                      type="password"
                      placeholder="留空保持不变"
                      value={ov.apiKey}
                      onChange={(e) => setAgentField(id, 'apiKey', e.target.value)}
                    />
                  </div>
                </div>
              );
            })}
            <div className="muted small" style={{ marginTop: 6 }}>
              留空 = 继承全局；要清除某智能体的覆盖，把模型留空保存即可。适合让个别角色走本地模型
              （如 Ollama）或另一家网关；覆盖仅当「模型提供方 = openai」时生效。
            </div>

            <div className="section-h">编排与门禁</div>
            <div className="form-grid">
              <div className="field">
                <label>Turn Budget</label>
                <input type="number" value={turnBudget} onChange={(e) => setTurnBudget(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>最大返工轮次</label>
                <input type="number" value={maxRevisions} onChange={(e) => setMaxRevisions(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>质量分阈值</label>
                <input
                  type="number"
                  value={qualityThreshold}
                  onChange={(e) => setQualityThreshold(Number(e.target.value))}
                />
              </div>
            </div>

            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={autoApprove}
                onChange={(e) => setAutoApprove(e.target.checked)}
                style={{ width: 'auto' }}
              />
              自动审批（新任务默认跳过人工裁决）
            </label>

            <div className="section-h">成本与缓存</div>
            <div className="form-grid">
              <div className="field">
                <label>单任务成本上限（USD）</label>
                <input
                  type="number"
                  min="0"
                  step="0.1"
                  value={costBudgetUsd}
                  onChange={(e) => setCostBudgetUsd(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>单任务 token 上限</label>
                <input type="number" min="0" value={tokenBudget} onChange={(e) => setTokenBudget(Number(e.target.value))} />
              </div>
            </div>
            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={llmCache}
                onChange={(e) => setLlmCache(e.target.checked)}
                style={{ width: 'auto' }}
              />
              启用 LLM 响应缓存（重复请求直接复用，省钱且零延迟）
            </label>
            <div className="muted small" style={{ marginTop: 6 }}>
              成本或 token 任一超限即触发熔断：后续步骤自动改用内置离线引擎，保证交付链路不中断。
            </div>

            <div className="section-h">记忆库向量检索</div>
            <div className="form-grid">
              <div className="field">
                <label>向量提供方</label>
                <select
                  value={embeddingProvider}
                  onChange={(e) =>
                    setEmbeddingProvider(e.target.value as PublicConfigView['embedding']['provider'])
                  }
                >
                  <option value="local">local（本地 hashing，零依赖可离线）</option>
                  <option value="openai">openai（兼容 /embeddings 的任意网关）</option>
                </select>
              </div>
              <div className="field">
                <label>Embedding 模型</label>
                <input value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)} />
              </div>
              <div className="field">
                <label>语义权重（0–1）</label>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={embeddingWeight}
                  onChange={(e) => setEmbeddingWeight(Number(e.target.value))}
                />
              </div>
            </div>
            {embeddingProvider === 'openai' ? (
              <div className="field" style={{ marginTop: 12 }}>
                <label>
                  Embedding API Key{' '}
                  {config.embedding.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      已设置 {config.embedding.apiKeyMasked}
                    </Chip>
                  ) : (
                    <Chip tone="tone-warn">留空则复用上面的模型 API Key</Chip>
                  )}
                </label>
                <input
                  type="password"
                  value={embeddingApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setEmbeddingApiKey(e.target.value)}
                />
              </div>
            ) : null}
            <div className="muted small" style={{ marginTop: 6 }}>
              权重 0 = 纯关键词检索（与历史行为一致）；调大后「换个说法也能召回」。远端不可用时自动回退本地向量。
            </div>

            <div className="section-h">发布投递</div>
            <div className="form-grid">
              <div className="field">
                <label>
                  发布 Webhook{' '}
                  {config.publish.webhookSet ? (
                    <Chip tone="tone-ok">已配置</Chip>
                  ) : (
                    <Chip tone="tone-idle">未配置</Chip>
                  )}
                </label>
                <input
                  value={publishWebhookUrl}
                  placeholder="https://gateway.example.com/publish"
                  onChange={(e) => setPublishWebhookUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>失败重试次数</label>
                <input
                  type="number"
                  min="0"
                  value={publishRetry}
                  onChange={(e) => setPublishRetry(Number(e.target.value))}
                />
              </div>
            </div>
            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={publishAutoDispatch}
                onChange={(e) => setPublishAutoDispatch(e.target.checked)}
                style={{ width: 'auto' }}
              />
              后台自动投递到期排期（每 {config.publish.tickSeconds}s 巡检一次）
            </label>
            <div className="muted small" style={{ marginTop: 6 }}>
              系统不内置各平台私有 SDK：到点把「该投什么」推给 webhook，由网关去调用平台开放接口。
              未配置 webhook 时，「投递」等价于「登记发布」，离线同样可用。
            </div>

            <div className="section-h">质量评估（LLM-as-a-Judge）</div>
            <div className="form-grid">
              <div className="field">
                <label>介入方式</label>
                <select
                  value={judgeMode}
                  onChange={(e) => setJudgeMode(e.target.value as PublicConfigView['judge']['mode'])}
                >
                  <option value="off">off（关闭评估）</option>
                  <option value="advisory">advisory（只打分，不影响门禁）</option>
                  <option value="blocking">blocking（低分参与返工判定）</option>
                </select>
              </div>
              <div className="field">
                <label>评估器</label>
                <select
                  value={judgeProvider}
                  onChange={(e) =>
                    setJudgeProvider(e.target.value as PublicConfigView['judge']['provider'])
                  }
                >
                  <option value="offline">offline（规则评估，零依赖可离线）</option>
                  <option value="llm">llm（走模型网关，失败自动回退）</option>
                </select>
              </div>
              <div className="field">
                <label>通过线（0–100）</label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={judgePassThreshold}
                  onChange={(e) => setJudgePassThreshold(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>计入综合质量分的权重（0–1）</label>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={judgeWeight}
                  onChange={(e) => setJudgeWeight(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
              <Chip tone="tone-idle" mono title="评分口径版本：跨版本对比分数前必须先对齐">
                {config.judge.rubric}
              </Chip>
              <Chip tone="tone-idle">六维：需求契合 / 合规安全 / 结构完整 / 品牌语气 / 事实稳妥 / 吸引力</Chip>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              默认 advisory：评估只产出分数与建议，不改变门禁结论；权重 0 表示评估分完全不影响综合质量分。
            </div>

            <div className="section-h">调用轨迹与 OTLP 导出</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Chip tone={config.tracing.otlpConfigured ? 'tone-ok' : 'tone-idle'}>
                {config.tracing.otlpConfigured ? 'OTLP 已配置' : '进程内追踪（未接 OTLP）'}
              </Chip>
              <Chip tone="tone-idle" mono>
                service {config.tracing.serviceName}
              </Chip>
              <Chip tone="tone-idle" mono title="采样只作用于导出面（OTLP + 落盘），进程内轨迹始终完整">
                采样 {config.tracing.sampler}
                {config.tracing.sampler.includes('traceidratio') ? ` @ ${config.tracing.sampleRatio}` : ''}
              </Chip>
              <Chip tone="tone-idle" mono title="W3C traceparent：入站 POST /api/tasks 解析，出站 webhook 携带">
                传播 W3C traceparent
              </Chip>
              {config.tracing.otlpEndpoint ? (
                <Chip tone="tone-info" mono>
                  {config.tracing.otlpEndpoint}
                </Chip>
              ) : null}
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              OTLP 端点通过环境变量 <code>OTLP_ENDPOINT</code> 配置（如
              <code> http://localhost:4318</code>），采样器与比例对应{' '}
              <code>OTEL_TRACES_SAMPLER</code> / <code>OTEL_TRACES_SAMPLER_ARG</code>，均不在此处热改。
              <strong>不配置也完整可用</strong>：进程内 span 树与
              <code> data/traces/*.json </code>不依赖任何外部服务；配置后会把同一份 span
              （相同的 trace_id / span_id）转发给 collector，可在 Jaeger 里直接查。
              本地一键起 Jaeger：<code>docker compose up -d</code>。
            </div>

            <div className="section-h">数字人渲染（开发样例）</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Chip tone={config.digitalHuman.provider === 'http' ? 'tone-info' : 'tone-idle'}>
                {config.digitalHuman.provider === 'http' ? 'http 适配网关' : 'sample 内置样例引擎'}
              </Chip>
              {config.digitalHuman.apiUrl ? (
                <Chip tone="tone-info" mono>
                  {config.digitalHuman.apiUrl}
                </Chip>
              ) : (
                <Chip tone="tone-idle">未配置远端网关（用内置引擎）</Chip>
              )}
              {config.digitalHuman.avatar ? <Chip tone="tone-idle">形象 {config.digitalHuman.avatar}</Chip> : null}
              {config.digitalHuman.apiKeySet ? <Chip tone="tone-ok">网关密钥已配置</Chip> : null}
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              数字人渲染<strong>不在本系统内实现</strong>：HeyGen / D-ID / 腾讯智影等服务的协议差异由你在自己的网关层消化。
              任务产出视频脚本后，「数字人渲染」面板会出现创建入口；环境变量 <code>DIGITAL_HUMAN_API_URL</code>（可选{' '}
              <code>DIGITAL_HUMAN_API_KEY</code> / <code>DIGITAL_HUMAN_AVATAR</code>）切换到对接自建渲染网关。
            </div>

            <div className="section-h">站点监控</div>
            <div className="field">
              <label>待爬页面 URL（一行一个，最多 20 条，创作时由 site_monitor 工具现场抓取）</label>
              <textarea
                rows={4}
                value={siteUrlsText}
                placeholder={'https://example.com/pricing\nhttps://competitor.com/blog'}
                onChange={(e) => setSiteUrlsText(e.target.value)}
              />
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              每次创作时 A1 / A2 / A3 / A6 / A10 会抓取这些页面的正文作为参考（仅限公开页面，
              每页最多注入 1200 字）。未配置时智能体如实声明「未配置站点监控」，不引用站点数据。
            </div>

            <div className="section-h">访问令牌{config.authRequired ? '（已启用鉴权）' : ''}</div>
            <div className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
              <div className="field" style={{ flex: 1 }}>
                <label>
                  API Token{' '}
                  {config.authRequired ? (
                    <Chip tone="tone-warn">服务端已开启鉴权</Chip>
                  ) : (
                    <Chip tone="tone-idle">服务端未开启</Chip>
                  )}
                </label>
                <input
                  type="password"
                  value={apiToken}
                  placeholder="服务端设置 CREATOR_API_TOKENS 后填写"
                  onChange={(e) => setApiTokenState(e.target.value)}
                />
              </div>
              <button className="btn btn-sm" onClick={saveToken}>
                保存令牌
              </button>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              令牌仅保存在本机浏览器，用于请求头 <code>X-API-Token</code>；不同令牌对应不同租户，任务互相不可见。
            </div>

            <div className="row" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? <Spinner /> : null} 保存配置
              </button>
            </div>
          </>
        ) : tab === 'knowledge' ? (
          <div style={{ marginTop: 4 }}>
            <KnowledgePanel knowledge={knowledge} />
          </div>
        ) : tab === 'memory' ? (
          <div style={{ marginTop: 4 }}>
            <MemoryPanel />
          </div>
        ) : (
          <div style={{ marginTop: 4 }}>
            <GoldenPanel onToast={onToast} />
          </div>
        )}
      </div>
    </div>
  );
}
