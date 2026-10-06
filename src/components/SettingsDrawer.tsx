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
  const [vision, setVision] = useState(config.llm.vision);
  const [visionMaxImages, setVisionMaxImages] = useState(config.llm.visionMaxImages);
  const [thinking, setThinking] = useState(config.llm.thinking);
  const [host, setHost] = useState(config.host);
  const [port, setPort] = useState(config.port);
  const [turnBudget, setTurnBudget] = useState(config.turnBudget);
  const [maxRevisions, setMaxRevisions] = useState(config.maxRevisions);
  const [qualityThreshold, setQualityThreshold] = useState(config.qualityThreshold);
  const [autoApprove, setAutoApprove] = useState(config.autoApprove);
  const [costBudgetUsd, setCostBudgetUsd] = useState(config.costBudgetUsd);
  const [tokenBudget, setTokenBudget] = useState(config.tokenBudget);
  const [digestMaxCalls, setDigestMaxCalls] = useState(config.digestMaxCalls);
  const [llmCache, setLlmCache] = useState(config.llmCache);
  const [embeddingProvider, setEmbeddingProvider] = useState(config.embedding.provider);
  const [embeddingBaseUrl, setEmbeddingBaseUrl] = useState(config.embedding.baseUrl);
  const [embeddingModel, setEmbeddingModel] = useState(config.embedding.model);
  const [embeddingDim, setEmbeddingDim] = useState(config.embedding.dim);
  const [embeddingWeight, setEmbeddingWeight] = useState(config.embedding.weight);
  const [embeddingApiKey, setEmbeddingApiKey] = useState('');
  const [publishWebhookUrl, setPublishWebhookUrl] = useState(config.publish.webhookUrl);
  const [publishAutoDispatch, setPublishAutoDispatch] = useState(config.publish.autoDispatch);
  const [publishRetry, setPublishRetry] = useState(config.publish.retry);
  const [judgeMode, setJudgeMode] = useState(config.judge.mode);
  const [judgeProvider, setJudgeProvider] = useState(config.judge.provider);
  const [judgeModel, setJudgeModel] = useState(config.judge.model);
  const [judgePassThreshold, setJudgePassThreshold] = useState(config.judge.passThreshold);
  const [judgeWeight, setJudgeWeight] = useState(config.judge.weight);
  const [dhProvider, setDhProvider] = useState(config.digitalHuman.provider);
  const [dhApiUrl, setDhApiUrl] = useState(config.digitalHuman.apiUrl);
  const [dhAvatar, setDhAvatar] = useState(config.digitalHuman.avatar);
  const [dhApiKey, setDhApiKey] = useState('');
  const [dhTimeoutMs, setDhTimeoutMs] = useState(config.digitalHuman.timeoutMs);
  const [imagegenProvider, setImagegenProvider] = useState(config.imageGen.provider);
  const [imagegenBaseUrl, setImagegenBaseUrl] = useState(config.imageGen.baseUrl);
  const [imagegenModel, setImagegenModel] = useState(config.imageGen.model);
  const [imagegenSize, setImagegenSize] = useState(config.imageGen.size);
  const [imagegenMaxImages, setImagegenMaxImages] = useState(config.imageGen.maxImages);
  const [imagegenApiKey, setImagegenApiKey] = useState('');
  const [imagegenTimeoutMs, setImagegenTimeoutMs] = useState(config.imageGen.timeoutMs);
  const [videogenProvider, setVideogenProvider] = useState(config.videoGen.provider);
  const [videogenApiUrl, setVideogenApiUrl] = useState(config.videoGen.apiUrl);
  const [videogenApiKey, setVideogenApiKey] = useState('');
  const [videogenTimeoutMs, setVideogenTimeoutMs] = useState(config.videoGen.timeoutMs);
  const [videounderstandProvider, setVideounderstandProvider] = useState(config.videoUnderstand.provider);
  const [videounderstandApiUrl, setVideounderstandApiUrl] = useState(config.videoUnderstand.apiUrl);
  const [videounderstandApiKey, setVideounderstandApiKey] = useState('');
  const [videounderstandTimeoutMs, setVideounderstandTimeoutMs] = useState(config.videoUnderstand.timeoutMs);
  const [petgenProvider, setPetgenProvider] = useState(config.petGen.provider);
  const [petgenFrameSize, setPetgenFrameSize] = useState(config.petGen.frameSize);
  const [petgenMaxFrames, setPetgenMaxFrames] = useState(config.petGen.maxFrames);
  const [petgenTimeoutMs, setPetgenTimeoutMs] = useState(config.petGen.timeoutMs);
  const [skillgenProvider, setSkillgenProvider] = useState(config.skillGen.provider);
  const [skillgenMaxWorks, setSkillgenMaxWorks] = useState(config.skillGen.maxWorks);
  const [searchProvider, setSearchProvider] = useState(config.search.provider);
  const [searchApiUrl, setSearchApiUrl] = useState(config.search.apiUrl);
  const [searchApiKey, setSearchApiKey] = useState('');
  const [searchMaxResults, setSearchMaxResults] = useState(config.search.maxResults);
  const [searchTimeoutMs, setSearchTimeoutMs] = useState(config.search.timeoutMs);
  const [searchFetchPages, setSearchFetchPages] = useState(config.search.fetchPages);
  const [searchMaxPages, setSearchMaxPages] = useState(config.search.maxPages);
  const [tracingOtlpEndpoint, setTracingOtlpEndpoint] = useState(config.tracing.otlpEndpoint);
  const [tracingServiceName, setTracingServiceName] = useState(config.tracing.serviceName);
  const [tracingOtlpHeaders, setTracingOtlpHeaders] = useState('');
  const [tracingSampler, setTracingSampler] = useState(config.tracing.sampler);
  const [tracingSampleRatio, setTracingSampleRatio] = useState(config.tracing.sampleRatio);
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
      vision,
      visionMaxImages,
      thinking,
      host: host.trim(),
      turnBudget,
      maxRevisions,
      qualityThreshold,
      autoApprove,
      costBudgetUsd,
      tokenBudget,
      digestMaxCalls,
      llmCache,
      embeddingProvider,
      embeddingBaseUrl: embeddingBaseUrl.trim(),
      embeddingModel,
      embeddingDim,
      embeddingWeight,
      publishWebhookUrl,
      publishAutoDispatch,
      publishRetry,
      judgeMode,
      judgeProvider,
      judgeModel: judgeModel.trim(),
      judgePassThreshold,
      judgeWeight,
      dhProvider,
      dhApiUrl: dhApiUrl.trim(),
      dhAvatar: dhAvatar.trim(),
      dhTimeoutMs,
      imagegenProvider,
      imagegenBaseUrl: imagegenBaseUrl.trim(),
      imagegenModel: imagegenModel.trim(),
      imagegenSize: imagegenSize.trim(),
      imagegenMaxImages,
      imagegenTimeoutMs,
      videogenProvider,
      videogenApiUrl: videogenApiUrl.trim(),
      videogenTimeoutMs,
      videounderstandProvider,
      videounderstandApiUrl: videounderstandApiUrl.trim(),
      videounderstandTimeoutMs,
      petgenProvider,
      petgenFrameSize,
      petgenMaxFrames,
      petgenTimeoutMs,
      skillgenProvider,
      skillgenMaxWorks,
      searchProvider,
      searchApiUrl: searchApiUrl.trim(),
      searchMaxResults,
      searchTimeoutMs,
      searchFetchPages,
      searchMaxPages,
      tracingOtlpEndpoint: tracingOtlpEndpoint.trim(),
      tracingServiceName: tracingServiceName.trim(),
      tracingSampler,
      tracingSampleRatio,
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
    const portNum = Number(port);
    if (Number.isInteger(portNum) && portNum >= 1 && portNum <= 65535) patch.port = portNum;
    if (apiKey.trim()) patch.apiKey = apiKey.trim();
    if (embeddingApiKey.trim()) patch.embeddingApiKey = embeddingApiKey.trim();
    if (dhApiKey.trim()) patch.dhApiKey = dhApiKey.trim();
    if (imagegenApiKey.trim()) patch.imagegenApiKey = imagegenApiKey.trim();
    if (videogenApiKey.trim()) patch.videogenApiKey = videogenApiKey.trim();
    if (videounderstandApiKey.trim()) patch.videounderstandApiKey = videounderstandApiKey.trim();
    if (searchApiKey.trim()) patch.searchApiKey = searchApiKey.trim();
    // OTLP 鉴权头仅输入非空时携带（缺失 = 保留已存值，防止误清）
    if (tracingOtlpHeaders.trim()) patch.tracingOtlpHeaders = tracingOtlpHeaders.trim();
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
              <div className="field">
                <label>深度思考</label>
                <select value={thinking} onChange={(e) => setThinking(e.target.value as 'enabled' | 'disabled')}>
                  <option value="disabled">disabled（默认，响应更快）</option>
                  <option value="enabled">enabled（网关支持时先生成推理）</option>
                </select>
              </div>
            </div>
            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={vision} onChange={(e) => setVision(e.target.checked)} style={{ width: 'auto' }} />
              多模态输入（Brief 附带图片时随请求发送；仅对支持读图的模型生效）
            </label>
            {vision ? (
              <div className="field" style={{ marginTop: 8 }}>
                <label>单次请求图片上限</label>
                <input
                  type="number"
                  min="1"
                  value={visionMaxImages}
                  onChange={(e) => setVisionMaxImages(Number(e.target.value))}
                />
              </div>
            ) : null}

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
              <div className="field">
                <label>素材研读调用上限</label>
                <input
                  type="number"
                  min="0"
                  value={digestMaxCalls}
                  onChange={(e) => setDigestMaxCalls(Number(e.target.value))}
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
                <label>Embedding Base URL</label>
                <input value={embeddingBaseUrl} placeholder="同全局端点可留空" onChange={(e) => setEmbeddingBaseUrl(e.target.value)} />
              </div>
              <div className="field">
                <label>向量维度</label>
                <input type="number" min="16" value={embeddingDim} onChange={(e) => setEmbeddingDim(Number(e.target.value))} />
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
                <label>评估模型</label>
                <input value={judgeModel} placeholder="留空继承全局模型" onChange={(e) => setJudgeModel(e.target.value)} />
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
            <div className="form-grid">
              <div className="field">
                <label>
                  OTLP 端点{' '}
                  {config.tracing.otlpConfigured ? (
                    <Chip tone="tone-ok">已配置</Chip>
                  ) : (
                    <Chip tone="tone-idle">未配置（仅进程内追踪）</Chip>
                  )}
                </label>
                <input
                  value={tracingOtlpEndpoint}
                  placeholder="http://localhost:4318"
                  onChange={(e) => setTracingOtlpEndpoint(e.target.value)}
                />
              </div>
              <div className="field">
                <label>服务名（Jaeger 中的标识）</label>
                <input value={tracingServiceName} onChange={(e) => setTracingServiceName(e.target.value)} />
              </div>
              <div className="field">
                <label>采样器</label>
                <select value={tracingSampler} onChange={(e) => setTracingSampler(e.target.value)}>
                  <option value="parentbased_always_on">parentbased_always_on（全采样）</option>
                  <option value="parentbased_traceidratio">parentbased_traceidratio（按比例）</option>
                  <option value="always_on">always_on</option>
                  <option value="always_off">always_off</option>
                  <option value="traceidratio">traceidratio</option>
                </select>
              </div>
              <div className="field">
                <label>采样比例（0–1）</label>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={tracingSampleRatio}
                  onChange={(e) => setTracingSampleRatio(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>
                  OTLP 鉴权头{' '}
                  {config.tracing.otlpHeadersSet ? (
                    <Chip tone="tone-ok" mono>
                      已设置
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={tracingOtlpHeaders}
                  placeholder="key1=value1,key2=value2（留空表示不修改）"
                  onChange={(e) => setTracingOtlpHeaders(e.target.value)}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              采样只作用于导出面（OTLP + 落盘），进程内轨迹始终完整。<strong>不配置也完整可用</strong>：进程内
              span 树与 <code>data/traces/*.json </code>不依赖任何外部服务；配置后会把同一份 span
              （相同的 trace_id / span_id）转发给 collector，可在 Jaeger 里直接查。
              本地一键起 Jaeger：<code>docker compose up -d</code>。
            </div>

            <div className="section-h">数字人渲染（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>渲染提供方</label>
                <select value={dhProvider} onChange={(e) => setDhProvider(e.target.value as 'sample' | 'http')}>
                  <option value="sample">sample（内置样例引擎，离线可用）</option>
                  <option value="http">http（自建渲染网关）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  网关 URL{' '}
                  {config.digitalHuman.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      密钥已设置 {config.digitalHuman.apiKeyMasked}
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={dhApiUrl}
                  placeholder="https://your-gateway/render"
                  onChange={(e) => setDhApiUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>形象（avatar）</label>
                <input value={dhAvatar} placeholder="网关侧的形象标识" onChange={(e) => setDhAvatar(e.target.value)} />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={dhTimeoutMs} onChange={(e) => setDhTimeoutMs(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>网关 API Key</label>
                <input
                  type="password"
                  value={dhApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setDhApiKey(e.target.value)}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              数字人渲染<strong>不在本系统内实现</strong>：HeyGen / D-ID / 腾讯智影等服务的协议差异由你在自己的网关层消化。
              任务产出视频脚本后，「数字人渲染」面板会出现创建入口；选 http 并填网关地址即对接自建渲染网关。
            </div>

            <div className="section-h">图片生成（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>生成提供方</label>
                <select
                  value={imagegenProvider}
                  onChange={(e) => setImagegenProvider(e.target.value as 'sample' | 'openai' | 'local')}
                >
                  <option value="sample">sample（内置样例引擎，只出清单不产真图）</option>
                  <option value="openai">openai（OpenAI 协议 /images/generations）</option>
                  <option value="local">local（A1111 / SD-WebUI 本机 txt2img）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  端点 URL{' '}
                  {config.imageGen.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      密钥已设置 {config.imageGen.apiKeyMasked}
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={imagegenBaseUrl}
                  placeholder="https://... 或 http://127.0.0.1:7860"
                  onChange={(e) => setImagegenBaseUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>模型（openai 生效）</label>
                <input value={imagegenModel} placeholder="留空用网关默认" onChange={(e) => setImagegenModel(e.target.value)} />
              </div>
              <div className="field">
                <label>出图尺寸</label>
                <input value={imagegenSize} placeholder="1024x1024" onChange={(e) => setImagegenSize(e.target.value)} />
              </div>
              <div className="field">
                <label>单次最多生成张数</label>
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={imagegenMaxImages}
                  onChange={(e) => setImagegenMaxImages(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={imagegenTimeoutMs} onChange={(e) => setImagegenTimeoutMs(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>API Key</label>
                <input
                  type="password"
                  value={imagegenApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setImagegenApiKey(e.target.value)}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              图片生成<strong>不在本系统内实现</strong>，只提供可回归的接入样例：任务产出视觉指导（visual_brief）后，
              「图片生成」面板会出现创建入口；local 私网端点按 0 元计。未配端点时 openai/local 通道<strong>显式失败、不假装成功</strong>。
            </div>

            <div className="section-h">视频生成（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>生成提供方</label>
                <select
                  value={videogenProvider}
                  onChange={(e) => setVideogenProvider(e.target.value as 'sample' | 'http')}
                >
                  <option value="sample">sample（内置样例引擎，只出清单不产真片）</option>
                  <option value="http">http（POST 建任务 → GET 查状态网关）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  网关 URL{' '}
                  {config.videoGen.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      密钥已设置 {config.videoGen.apiKeyMasked}
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={videogenApiUrl}
                  placeholder="https://your-videogen-gateway/render"
                  onChange={(e) => setVideogenApiUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={videogenTimeoutMs} onChange={(e) => setVideogenTimeoutMs(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>网关 API Key</label>
                <input
                  type="password"
                  value={videogenApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setVideogenApiKey(e.target.value)}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              视频生成只按 <code>video_script</code> 分镜产出画面段（B-roll），<strong>不做拼接合成</strong>；
              因生成昂贵，会按镜数估算并纳入<strong>成本预算熔断</strong>：超预算 fail-loud，本地私网端点计 0 元。
            </div>

            <div className="section-h">视频理解（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>理解提供方</label>
                <select
                  value={videounderstandProvider}
                  onChange={(e) => setVideounderstandProvider(e.target.value as 'sample' | 'real')}
                >
                  <option value="sample">sample（内置样例引擎，只出占位骨架不真理解）</option>
                  <option value="real">real（整集视频 → 原生视频理解网关）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  网关 URL{' '}
                  {config.videoUnderstand.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      密钥已设置 {config.videoUnderstand.apiKeyMasked}
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={videounderstandApiUrl}
                  placeholder="https://your-video-understanding-gateway/analyze"
                  onChange={(e) => setVideounderstandApiUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={videounderstandTimeoutMs} onChange={(e) => setVideounderstandTimeoutMs(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>网关 API Key</label>
                <input
                  type="password"
                  value={videounderstandApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setVideounderstandApiKey(e.target.value)}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              视频理解把<strong>整集视频</strong>交给<strong>原生支持视频输入的理解网关</strong>（一集=一次调用）产出该集视觉摘要；
              系统只传 <code>assets/</code> 本地引用、<strong>不搬运大文件</strong>；未配网关或非本地视频时 real 通道<strong>如实失败、不假装看懂</strong>，
              按集预估成本纳入<strong>预算熔断</strong>。
            </div>

            <div className="section-h">桌面宠物（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>
                  出帧提供方{' '}
                  {config.petGen.configured ? (
                    <Chip tone="tone-ok" mono>
                      真实通道可用
                    </Chip>
                  ) : (
                    <Chip tone="tone-bad" mono>
                      图片端点未配置
                    </Chip>
                  )}
                </label>
                <select
                  value={petgenProvider}
                  onChange={(e) => setPetgenProvider(e.target.value as 'sample' | 'imagegen')}
                >
                  <option value="sample">sample（标准库离线画帧，零依赖）</option>
                  <option value="imagegen">imagegen（复用图片生成端点逐帧出真图）</option>
                </select>
              </div>
              <div className="field">
                <label>帧边长（px）</label>
                <input type="number" min={32} max={192} value={petgenFrameSize} onChange={(e) => setPetgenFrameSize(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>单次最多帧数</label>
                <input type="number" min={1} max={24} value={petgenMaxFrames} onChange={(e) => setPetgenMaxFrames(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>出帧超时（ms）</label>
                <input type="number" value={petgenTimeoutMs} onChange={(e) => setPetgenTimeoutMs(Number(e.target.value))} />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              桌宠<strong>不做抠图与 sprite sheet 切片</strong>：帧背景统一为透明键纯色，运行器按窗口
              <code>-transparentcolor</code> 抠掉。<code>imagegen</code> 通道复用上面的{' '}
              <code>IMAGEGEN_*</code> 端点与密钥<strong>逐帧</strong>出图（一帧=一次调用），
              按帧数预估纳入<strong>成本预算熔断</strong>；未配置端点时<strong>显式失败、不假装出图</strong>。
            </div>

            <div className="section-h">创作技能提炼（开发样例）</div>
            <div className="form-grid">
              <div className="field">
                <label>
                  提炼通道{' '}
                  {config.skillGen.configured ? (
                    <Chip tone="tone-ok" mono>
                      当前通道可用
                    </Chip>
                  ) : (
                    <Chip tone="tone-bad" mono>
                      LLM 网关未配置
                    </Chip>
                  )}
                </label>
                <select
                  value={skillgenProvider}
                  onChange={(e) => setSkillgenProvider(e.target.value as 'rules' | 'llm')}
                >
                  <option value="rules">rules（量化统计作品正文，零 token、不含风格判断）</option>
                  <option value="llm">llm（复用上面的 LLM_* 网关做带判断力的提炼）</option>
                </select>
              </div>
              <div className="field">
                <label>单次最多作品数</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={skillgenMaxWorks}
                  onChange={(e) => setSkillgenMaxWorks(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              技能从<strong>你自己的作品</strong>里提炼（Brief 的文档 / 字幕素材、已产出的文案与视频脚本、
              视频理解摘要）。<code>rules</code> 通道句长 / 段落 / 语速等数字<strong>真统计</strong>自正文，
              但不含模型判断，故产物标注 <code>simulated=true</code>；<code>llm</code> 通道一次=一次调用，
              <strong>复用 </strong><code>LLM_*</code><strong> 端点与密钥、不新增配置</strong>，
              预估成本纳入<strong>预算熔断</strong>，未配真实网关时<strong>显式失败、不假装提炼</strong>。
            </div>

            <div className="section-h">联网检索（可选，默认关闭）</div>
            <div className="form-grid">
              <div className="field">
                <label>检索提供方</label>
                <select value={searchProvider} onChange={(e) => setSearchProvider(e.target.value as 'none' | 'http')}>
                  <option value="none">none（关闭联网，智能体如实声明未联网）</option>
                  <option value="http">http（自建搜索网关，如 SearXNG）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  搜索网关 URL{' '}
                  {config.search.configured ? (
                    <Chip tone="tone-ok">已联网</Chip>
                  ) : (
                    <Chip tone="tone-idle">未联网</Chip>
                  )}
                </label>
                <input
                  value={searchApiUrl}
                  placeholder="https://search.example.com/search"
                  onChange={(e) => setSearchApiUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>返回条数上限</label>
                <input
                  type="number"
                  min="1"
                  value={searchMaxResults}
                  onChange={(e) => setSearchMaxResults(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input
                  type="number"
                  min="1000"
                  value={searchTimeoutMs}
                  onChange={(e) => setSearchTimeoutMs(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>单任务最多抓取页数</label>
                <input
                  type="number"
                  min="0"
                  value={searchMaxPages}
                  onChange={(e) => setSearchMaxPages(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>网关 API Key</label>
                <input
                  type="password"
                  value={searchApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setSearchApiKey(e.target.value)}
                />
              </div>
            </div>
            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={searchFetchPages}
                onChange={(e) => setSearchFetchPages(e.target.checked)}
                style={{ width: 'auto' }}
              />
              允许抓取搜索结果页面正文（page_fetch 路径，正文截断注入）
            </label>
            <div className="muted small" style={{ marginTop: 6 }}>
              系统不绑定任何搜索厂商：网关只需返回 JSON 结果列表，由你的网关层对接 SearXNG /
              各搜索引擎 API。未联网时智能体禁止引用在线数据。
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

            <div className="section-h">服务监听（重启后生效）</div>
            <div className="form-grid">
              <div className="field">
                <label>监听地址</label>
                <input value={host} placeholder="127.0.0.1 / 0.0.0.0" onChange={(e) => setHost(e.target.value)} />
              </div>
              <div className="field">
                <label>端口</label>
                <input type="number" min="1" max="65535" value={port} onChange={(e) => setPort(Number(e.target.value))} />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              启动期参数：保存后写入持久化快照，<strong>下次启动服务时生效</strong>；本页其余修改均即时生效。
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
