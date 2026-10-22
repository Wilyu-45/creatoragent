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
  const [dubbingProvider, setDubbingProvider] = useState(config.dubbing.provider);
  const [dubbingApiUrl, setDubbingApiUrl] = useState(config.dubbing.apiUrl);
  const [dubbingApiKey, setDubbingApiKey] = useState('');
  const [dubbingTimeoutMs, setDubbingTimeoutMs] = useState(config.dubbing.timeoutMs);
  const [dubbingMaxLines, setDubbingMaxLines] = useState(config.dubbing.maxLines);
  const [dubbingCharsPerSecond, setDubbingCharsPerSecond] = useState(config.dubbing.charsPerSecond);
  const [dubbingSovitsBaseUrl, setDubbingSovitsBaseUrl] = useState(config.dubbing.sovitsBaseUrl);
  const [dubbingSovitsLanguage, setDubbingSovitsLanguage] = useState(config.dubbing.sovitsLanguage);
  const [dubbingElevenlabsBaseUrl, setDubbingElevenlabsBaseUrl] = useState(config.dubbing.elevenlabsBaseUrl);
  const [dubbingElevenlabsApiKey, setDubbingElevenlabsApiKey] = useState('');
  const [dubbingElevenlabsModel, setDubbingElevenlabsModel] = useState(config.dubbing.elevenlabsModel);
  const [dubbingSapiVoices, setDubbingSapiVoices] = useState(config.dubbing.sapiVoices);
  const [petgenProvider, setPetgenProvider] = useState(config.petGen.provider);
  const [petgenFrameSize, setPetgenFrameSize] = useState(config.petGen.frameSize);
  const [petgenMaxFrames, setPetgenMaxFrames] = useState(config.petGen.maxFrames);
  const [petgenTimeoutMs, setPetgenTimeoutMs] = useState(config.petGen.timeoutMs);
  const [skillgenProvider, setSkillgenProvider] = useState(config.skillGen.provider);
  const [skillgenMaxWorks, setSkillgenMaxWorks] = useState(config.skillGen.maxWorks);
  const [surfProvider, setSurfProvider] = useState(config.surf.provider);
  const [surfHeadless, setSurfHeadless] = useState(config.surf.headless);
  const [surfAutoEnabled, setSurfAutoEnabled] = useState(config.surf.autoEnabled);
  const [surfTickSeconds, setSurfTickSeconds] = useState(config.surf.tickSeconds);
  const [surfPoolMax, setSurfPoolMax] = useState(config.surf.poolMax);
  const [surfCardsPerSession, setSurfCardsPerSession] = useState(config.surf.cardsPerSession);
  const [surfAllowedDomainsText, setSurfAllowedDomainsText] = useState(config.surf.allowedDomains.join('\n'));
  const [surfSeedSitesText, setSurfSeedSitesText] = useState(config.surf.seedSites.join('\n'));
  const [surfTopicsText, setSurfTopicsText] = useState(config.surf.topics.join('\n'));
  const [surfWatchVideos, setSurfWatchVideos] = useState(config.surf.watchVideos);
  const [surfScrapeComments, setSurfScrapeComments] = useState(config.surf.scrapeComments);
  const [surfVideoFrames, setSurfVideoFrames] = useState(config.surf.videoFrames);
  const [surfVideoFrameIntervalMs, setSurfVideoFrameIntervalMs] = useState(config.surf.videoFrameIntervalMs);
  const [surfVlmGroupFrames, setSurfVlmGroupFrames] = useState(config.surf.vlmGroupFrames);
  const [surfMaxVideosPerSession, setSurfMaxVideosPerSession] = useState(config.surf.maxVideosPerSession);
  const [surfMaxComments, setSurfMaxComments] = useState(config.surf.maxComments);
  const [surfCropToVideo, setSurfCropToVideo] = useState(config.surf.cropToVideo);
  const [surfClickIntoVideos, setSurfClickIntoVideos] = useState(config.surf.clickIntoVideos);
  const [surfHumanPace, setSurfHumanPace] = useState(config.surf.humanPace);
  const [surfSearchOnSeeds, setSurfSearchOnSeeds] = useState(config.surf.searchOnSeeds);
  const [surfHopsPerPage, setSurfHopsPerPage] = useState(config.surf.hopsPerPage);
  const [surfListPages, setSurfListPages] = useState(config.surf.listPages);
  const [surfPageComments, setSurfPageComments] = useState(config.surf.pageComments);
  const [surfPageGapMs, setSurfPageGapMs] = useState(config.surf.pageGapMs);
  const [surfTopicFocus, setSurfTopicFocus] = useState(config.surf.topicFocus);
  const [surfSearchRetries, setSurfSearchRetries] = useState(config.surf.searchRetries);
  const [surfUseSuggestions, setSurfUseSuggestions] = useState(config.surf.useSuggestions);
  const [surfSuggestionsMax, setSurfSuggestionsMax] = useState(config.surf.suggestionsMax);
  const [surfAvoidWalls, setSurfAvoidWalls] = useState(config.surf.avoidWalls);
  const [surfRememberRead, setSurfRememberRead] = useState(config.surf.rememberRead);
  const [surfHistoryMax, setSurfHistoryMax] = useState(config.surf.historyMax);
  const [surfMaxPages, setSurfMaxPages] = useState(config.surf.maxPages);
  const [surfScrollRounds, setSurfScrollRounds] = useState(config.surf.scrollRounds);
  const [surfSettleMs, setSurfSettleMs] = useState(config.surf.settleMs);
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
      dubbingProvider,
      dubbingApiUrl: dubbingApiUrl.trim(),
      dubbingTimeoutMs,
      dubbingMaxLines,
      dubbingCharsPerSecond,
      dubbingSovitsBaseUrl: dubbingSovitsBaseUrl.trim(),
      dubbingSovitsLanguage: dubbingSovitsLanguage.trim() || 'zh',
      dubbingElevenlabsBaseUrl: dubbingElevenlabsBaseUrl.trim(),
      dubbingElevenlabsModel: dubbingElevenlabsModel.trim(),
      dubbingSapiVoices: dubbingSapiVoices.trim(),
      petgenProvider,
      petgenFrameSize,
      petgenMaxFrames,
      petgenTimeoutMs,
      skillgenProvider,
      skillgenMaxWorks,
      surfProvider,
      surfHeadless,
      surfAutoEnabled,
      surfTickSeconds,
      surfPoolMax,
      surfCardsPerSession,
      surfWatchVideos,
      surfScrapeComments,
      surfVideoFrames,
      surfVideoFrameIntervalMs,
      surfVlmGroupFrames,
      surfMaxVideosPerSession,
      surfMaxComments,
      surfCropToVideo,
      surfClickIntoVideos,
      surfHumanPace,
      surfSearchOnSeeds,
      surfHopsPerPage,
      surfListPages,
      surfPageComments,
      surfPageGapMs,
      surfTopicFocus,
      surfSearchRetries,
      surfUseSuggestions,
      surfSuggestionsMax,
      surfAvoidWalls,
      surfRememberRead,
      surfHistoryMax,
      surfMaxPages,
      surfScrollRounds,
      surfSettleMs,
      surfAllowedDomains: surfAllowedDomainsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      surfSeedSites: surfSeedSitesText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      surfTopics: surfTopicsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
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
    if (dubbingApiKey.trim()) patch.dubbingApiKey = dubbingApiKey.trim();
    if (dubbingElevenlabsApiKey.trim()) patch.dubbingElevenlabsApiKey = dubbingElevenlabsApiKey.trim();
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
                <label>轮次预算</label>
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

            <div className="section-h">全自动配音（真实功能：引擎逐句合成真音频）</div>
            <div className="form-grid">
              <div className="field">
                <label>
                  配音引擎{' '}
                  {config.dubbing.configured ? (
                    <Chip tone="tone-ok" mono>
                      当前通道可用
                    </Chip>
                  ) : (
                    <Chip tone="tone-bad" mono>
                      所选通道缺配置
                    </Chip>
                  )}
                </label>
                <select
                  value={dubbingProvider}
                  onChange={(e) => setDubbingProvider(e.target.value as typeof dubbingProvider)}
                >
                  <option value="auto">auto（按 elevenlabs→sovits→sapi→real 择优，都没配则 sample）</option>
                  <option value="sapi">sapi（Windows 系统音色本机真合成，不克隆参考音频）</option>
                  <option value="sovits">sovits（自建 GPT-SoVITS 类服务，参考音频零样本克隆）</option>
                  <option value="elevenlabs">elevenlabs（云端克隆，按字符计费）</option>
                  <option value="real">real（自定义配音网关，POST/GET 契约）</option>
                  <option value="sample">sample（离线模拟，不出真音频，联调用）</option>
                </select>
              </div>
              <div className="field">
                <label>
                  sovits 服务地址{' '}
                  {config.dubbing.engines.sovits ? <Chip tone="tone-ok" mono>已配置</Chip> : null}
                </label>
                <input
                  value={dubbingSovitsBaseUrl}
                  placeholder="http://127.0.0.1:9880（GPT-SoVITS api_v2）"
                  onChange={(e) => setDubbingSovitsBaseUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>sovits 文本语种（text_lang）</label>
                <input
                  value={dubbingSovitsLanguage}
                  placeholder="zh / en / ja"
                  onChange={(e) => setDubbingSovitsLanguage(e.target.value)}
                />
              </div>
              <div className="field">
                <label>
                  elevenlabs API 地址{' '}
                  {config.dubbing.elevenlabsApiKeySet ? (
                    <Chip tone="tone-ok" mono>克隆密钥已设置</Chip>
                  ) : null}
                </label>
                <input
                  value={dubbingElevenlabsBaseUrl}
                  placeholder="https://api.elevenlabs.io/v1"
                  onChange={(e) => setDubbingElevenlabsBaseUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>elevenlabs API Key（xi-api-key）</label>
                <input
                  type="password"
                  value={dubbingElevenlabsApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setDubbingElevenlabsApiKey(e.target.value)}
                />
              </div>
              <div className="field">
                <label>elevenlabs 合成模型</label>
                <input
                  value={dubbingElevenlabsModel}
                  placeholder="eleven_multilingual_v2"
                  onChange={(e) => setDubbingElevenlabsModel(e.target.value)}
                />
              </div>
              <div className="field">
                <label>sapi 限定音色（逗号分隔，空=全部）</label>
                <input
                  value={dubbingSapiVoices}
                  placeholder="如 Microsoft Huihui Desktop,Microsoft Zira Desktop"
                  onChange={(e) => setDubbingSapiVoices(e.target.value)}
                />
              </div>
              <div className="field">
                <label>
                  配音网关 URL{' '}
                  {config.dubbing.apiKeySet ? (
                    <Chip tone="tone-ok" mono>
                      网关密钥已设置 {config.dubbing.apiKeyMasked}
                    </Chip>
                  ) : null}
                </label>
                <input
                  value={dubbingApiUrl}
                  placeholder="https://your-dubbing-gateway/synthesize"
                  onChange={(e) => setDubbingApiUrl(e.target.value)}
                />
              </div>
              <div className="field">
                <label>超时（ms）</label>
                <input type="number" value={dubbingTimeoutMs} onChange={(e) => setDubbingTimeoutMs(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>网关 API Key</label>
                <input
                  type="password"
                  value={dubbingApiKey}
                  placeholder="留空表示不修改"
                  onChange={(e) => setDubbingApiKey(e.target.value)}
                />
              </div>
              <div className="field">
                <label>单作业最多行数</label>
                <input type="number" min={1} max={1000} value={dubbingMaxLines} onChange={(e) => setDubbingMaxLines(Number(e.target.value))} />
              </div>
              <div className="field">
                <label>估算语速（字/秒）</label>
                <input type="number" step={0.5} min={0.5} value={dubbingCharsPerSecond} onChange={(e) => setDubbingCharsPerSecond(Number(e.target.value))} />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              配音输入的剧情文本与参考音频都由用户提供；台词切分与说话人识别<strong>真做</strong>，
              引擎通道（sapi/sovits/elevenlabs）由后台 worker <strong>逐句真合成并落盘</strong>，
              完成后时间轴按音频实长重建、可逐句试听与下载整轨；服务重启后作业自动复活续跑。
              sapi 用系统音色<strong>不克隆参考音频</strong>（汇总里如实标注）；sovits/elevenlabs/real
              缺参考音频即<strong>显式失败、不假装克隆</strong>；云端引擎按计费口径纳入<strong>成本预算熔断</strong>。
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

            <div className="section-h">灵感冲浪（像真人上网找灵感 → 沉淀 idea 库）</div>
            <div className="form-grid">
              <div className="field">
                <label>
                  冲浪通道{' '}
                  {config.surf.configured ? (
                    <Chip tone="tone-ok" mono>
                      当前通道可用
                    </Chip>
                  ) : (
                    <Chip tone="tone-bad" mono>
                      当前通道缺配置
                    </Chip>
                  )}
                </label>
                <select
                  value={surfProvider}
                  onChange={(e) => setSurfProvider(e.target.value as 'sample' | 'auto' | 'browser' | 'http')}
                >
                  <option value="sample">sample（离线占位灵感，零依赖、不联网）</option>
                  <option value="auto">auto（按 browser→http→sample 自动择优）</option>
                  <option value="browser">browser（Playwright 真无头浏览器）</option>
                  <option value="http">http（复用联网检索网关冲浪）</option>
                </select>
              </div>
              <div className="field">
                <label>每轮产出灵感卡数</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={surfCardsPerSession}
                  onChange={(e) => setSurfCardsPerSession(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>灵感池最多沉淀（张）</label>
                <input
                  type="number"
                  min={1}
                  value={surfPoolMax}
                  onChange={(e) => setSurfPoolMax(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>自主冲浪间隔（秒）</label>
                <input
                  type="number"
                  min={30}
                  value={surfTickSeconds}
                  onChange={(e) => setSurfTickSeconds(Number(e.target.value))}
                />
              </div>
            </div>
            <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfHeadless}
                onChange={(e) => setSurfHeadless(e.target.checked)}
                style={{ width: 'auto' }}
              />
              无头模式（browser 通道不弹出可见浏览器窗口）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfAutoEnabled}
                onChange={(e) => setSurfAutoEnabled(e.target.checked)}
                style={{ width: 'auto' }}
              />
              开启后台定时自主冲浪（按下方主题列表定时囤灵感；默认关闭以保持进程零副作用、无外网连接）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfHumanPace}
                onChange={(e) => setSurfHumanPace(e.target.checked)}
                style={{ width: 'auto' }}
              />
              真人节奏：每轮下滑幅度与停留随机浮动，偶尔原地停一下把这段读完再滚（关掉 = 等距滚 + 固定等的机器节奏）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfSearchOnSeeds}
                onChange={(e) => setSurfSearchOnSeeds(e.target.checked)}
                style={{ width: 'auto' }}
              />
              站内搜索：像真人那样用起点站自己的搜索框逐字打字搜主题，再读结果页（找不到搜索框就如实不搜）；
              结果页标题里一个主题词都认不出时，会<strong>清空搜索框换个更短的说法再搜</strong>（换几次见下面的数字，搜过的每版关键词都记在信号上）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfUseSuggestions}
                onChange={(e) => setSurfUseSuggestions(e.target.checked)}
                style={{ width: 'auto' }}
              />
              也用站点给的搜索联想词：打字时弹出的下拉里，只点<strong>认得出本次主题词</strong>的那一条（点不动就把那个词打回框里再回车）；
              一条都认不出就照原词回车，<strong>绝不为了点一条联想把主题改掉</strong>，也没联想层时绝不假装看到过（关掉 = 一律按原词回车）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfAvoidWalls}
                onChange={(e) => setSurfAvoidWalls(e.target.checked)}
                style={{ width: 'auto' }}
              />
              读到门口就退出来：页面短到像一扇门且写着「请先登录 / 付费阅读 / 请输入验证码 / 页面不存在」时，
              信号上标出<strong>被哪种墙挡住</strong>、摘录只留门口那几句话，<strong>不绕墙也不把墙话当正文</strong>喂给模型
              （正常文章里顺口提一句「登录同步收藏」不会误判；关掉 = 墙上的话照旧当正文读）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfTopicFocus}
                onChange={(e) => setSurfTopicFocus(e.target.checked)}
                style={{ width: 'auto' }}
              />
              扫标题挑条目与视频：信息流里先点标题认得出主题词的那几条（要在线看的视频也照样挑，信号上写明凭哪几个词点进去；关掉 = 按页面原顺序）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfPageComments}
                onChange={(e) => setSurfPageComments(e.target.checked)}
                style={{ width: 'auto' }}
              />
              读全文与评论区：内容页先点开「展开全文」再读，并像真人那样翻到底逐条抓评论区（抓不到就留空，绝不编造）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfRememberRead}
                onChange={(e) => setSurfRememberRead(e.target.checked)}
                style={{ width: 'auto' }}
              />
              记住读过哪些页（跨会话）：上几轮真读进去的条目这轮就<strong>跳过、改读没见过的</strong>，
              整版都读过时照样重读并如实说明；关掉 = 每轮都当第一次刷这些站（灵感面板里可清掉这份记忆）
            </label>
            <div className="form-grid" style={{ marginTop: 8 }}>
              <div className="field">
                <label>每页点进几条内容页（多跳）</label>
                <input
                  type="number"
                  min={0}
                  max={10}
                  value={surfHopsPerPage}
                  onChange={(e) => setSurfHopsPerPage(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>一个列表最多刷几页（1 = 不翻页）</label>
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={surfListPages}
                  onChange={(e) => setSurfListPages(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>搜不对题就换个说法再搜几次（0 = 整句搜一次就完）</label>
                <input
                  type="number"
                  min={0}
                  max={3}
                  value={surfSearchRetries}
                  onChange={(e) => setSurfSearchRetries(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>一次最多读回几条搜索联想</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={surfSuggestionsMax}
                  onChange={(e) => setSurfSuggestionsMax(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>最多记住几页读过的（超了先忘最旧的）</label>
                <input
                  type="number"
                  min={20}
                  max={5000}
                  step={20}
                  value={surfHistoryMax}
                  onChange={(e) => setSurfHistoryMax(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>开新页前停顿（毫秒，真人节奏在此上下浮动）</label>
                <input
                  type="number"
                  min={0}
                  max={30000}
                  step={100}
                  value={surfPageGapMs}
                  onChange={(e) => setSurfPageGapMs(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>单次冲浪页数上限</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={surfMaxPages}
                  onChange={(e) => setSurfMaxPages(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>每页下滑轮数</label>
                <input
                  type="number"
                  min={0}
                  max={10}
                  value={surfScrollRounds}
                  onChange={(e) => setSurfScrollRounds(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>每轮停留（毫秒，真人节奏在此上下浮动）</label>
                <input
                  type="number"
                  min={0}
                  max={10000}
                  step={100}
                  value={surfSettleMs}
                  onChange={(e) => setSurfSettleMs(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              起点站首页、站内搜索结果页、<strong>点「下一页/加载更多」翻出的列表后续页</strong>、信息流点进的条目页、
              在线看的视频页<strong>合计不超过页数上限</strong>；翻页只点站点自己的控件，没这个控件或点了页面没变就如实停下（不硬拼 URL），
              多跳只点白名单内的同域条目（外站/内网/图片直链一律不收），挑条目时标题认不出主题词会如实写明是顺路读的。
            </div>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfWatchVideos}
                onChange={(e) => setSurfWatchVideos(e.target.checked)}
                style={{ width: 'auto' }}
              />
              看懂视频（仅 browser 生效）：像真人进视频页→关遮罩点播放→校验真的在播→播放中**分组截图**
              抽帧→逐组交 VLM 看懂并汇总（不下载视频文件）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfScrapeComments}
                onChange={(e) => setSurfScrapeComments(e.target.checked)}
                style={{ width: 'auto' }}
              />
              抓取视频页文字信息（标题 / 简介 / UP 主 / 播放量 / 逐条评论走 DOM 直抓；抓不到的靠截图 OCR）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfClickIntoVideos}
                onChange={(e) => setSurfClickIntoVideos(e.target.checked)}
                style={{ width: 'auto' }}
              />
              像真人一样从信息流点进视频页（浏览起点站时收集页内视频链接，逐条进入在线看）
            </label>
            <label className="row small" style={{ marginTop: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={surfCropToVideo}
                onChange={(e) => setSurfCropToVideo(e.target.checked)}
                style={{ width: 'auto' }}
              />
              抽帧只截播放器画面元素（含 iframe 内播放器）；关掉则整页截图（信息多、画面占比小）
            </label>
            <div className="form-grid" style={{ marginTop: 8 }}>
              <div className="field">
                <label>每视频抽帧数（交 VLM 的截图上限）</label>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={surfVideoFrames}
                  onChange={(e) => setSurfVideoFrames(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>抽帧间隔（毫秒）</label>
                <input
                  type="number"
                  min={0}
                  step={100}
                  value={surfVideoFrameIntervalMs}
                  onChange={(e) => setSurfVideoFrameIntervalMs(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>每组交 VLM 的截图张数</label>
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={surfVlmGroupFrames}
                  onChange={(e) => setSurfVlmGroupFrames(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>单会话最多看懂几个视频</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={surfMaxVideosPerSession}
                  onChange={(e) => setSurfMaxVideosPerSession(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label>每页最多收集评论条数</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={surfMaxComments}
                  onChange={(e) => setSurfMaxComments(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="muted small" style={{ marginTop: 8 }}>
              在线看视频的前置：Playwright {config.surf.browserAvailable ? '已安装' : '未安装（pip install playwright && playwright install chromium）'}
              {' · '}域名白名单 {config.surf.allowedDomains.length ? `已配 ${config.surf.allowedDomains.length} 项` : '未配（browser 通道受理即诚实失败）'}
              {' · '}图像输入 {config.surf.visionConfigured ? '已启用（LLM_VISION）' : '未启用：抽帧照做但**不交 VLM**，画面只如实标「未看懂」'}
            </div>
            <div className="field" style={{ marginTop: 8 }}>
              <label>可浏览域名白名单（一行一个，browser 通道只访问这些站点；SSRF 私网/环回一律拒绝）</label>
              <textarea
                rows={3}
                value={surfAllowedDomainsText}
                placeholder={'example.com\nreddit.com'}
                onChange={(e) => setSurfAllowedDomainsText(e.target.value)}
              />
            </div>
            <div className="field" style={{ marginTop: 8 }}>
              <label>起点站 URL（一行一个，browser 通道从这里出发navigate 浏览）</label>
              <textarea
                rows={3}
                value={surfSeedSitesText}
                placeholder={'https://www.example.com/trending'}
                onChange={(e) => setSurfSeedSitesText(e.target.value)}
              />
            </div>
            <div className="field" style={{ marginTop: 8 }}>
              <label>自主冲浪主题（一行一个，开启后台冲浪时每个主题囤一轮）</label>
              <textarea
                rows={3}
                value={surfTopicsText}
                placeholder={'夏季露营装备测评\n城市咖啡探店'}
                onChange={(e) => setSurfTopicsText(e.target.value)}
              />
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              默认 <code>sample</code>、不开自动冲浪：开箱即离线可跑、零外网连接。<code>browser</code> 通道需自行安装
              Playwright（<code>pip install playwright && playwright install chromium</code>，当前服务
              {config.surf.browserAvailable ? (
                <Chip tone="tone-ok">已检测到 Playwright</Chip>
              ) : (
                <Chip tone="tone-idle">未检测到 Playwright</Chip>
              )}
              ）并配置域名白名单与起点站；未装依赖 / 未配白名单时受理即<strong>如实失败并点名</strong>，绝不假装浏览过。
              browser 通道<strong>像真人那样浏览</strong>：随机节奏下滑刷信息流、用起点站自己的搜索框搜主题、
              点站点自己的「下一页/加载更多」接着刷、内容页点开「展开全文」并顺手翻评论区，
              再从条目里<strong>扫标题挑同域内容页</strong>点进读一层、要在线看的视频也从贴主题的标题里挑，搜出来的结果不贴主题就
              <strong>换个更短的说法再搜</strong>，这轮读过的条目还会<strong>记进跨会话浏览历史</strong>（下几轮跳过它们改读没见过的），
              每条信号都标注入口（首页 / 站内搜索 / 信息流点进 / 指定链接）
              与列表页码、翻页控件、页间停顿、点进理由（标题里认出的主题词）、站内搜索关键词等浏览证据。
              开启「看懂视频」后，<code>browser</code> 会在线播放视频、按间隔<strong>截图抽帧</strong>交给多模态网关
              （<code>LLM_*</code>）看懂，并把画面里的文字当 OCR 转写；<strong>不下载视频文件</strong>，也没配视觉网关/离线时
              画面摘要会自报「占位看懂」、绝不假装看懂。未开启时视频只作<strong>信号</strong>记录。
              灵感<strong>不自动交付智能体</strong>：入 idea 库后由你在冲浪面板勾选、调整再注入 Brief。
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
