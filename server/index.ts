import { existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import express from 'express';
import { createApiRouter } from './api/routes.ts';
import { getConfig, ROOT_DIR } from './config.ts';
import { blackboard } from './core/blackboard.ts';
import { taskStore } from './core/store.ts';
import { resolveProvider } from './llm/index.ts';
import { createLogger } from './logger.ts';

const log = createLogger('server');

async function bootstrap(): Promise<void> {
  const config = getConfig();
  const app = express();

  app.use(express.json({ limit: '4mb' }));
  app.use((req, _res, next) => {
    if (req.path.startsWith('/api')) log.debug(`${req.method} ${req.path}`);
    next();
  });
  app.use('/api', createApiRouter());

  // 生产模式下托管已构建的前端产物
  const distDir = path.join(ROOT_DIR, 'dist');
  if (existsSync(distDir)) {
    app.use(express.static(distDir));
    app.use((req, res, next) => {
      if (req.method !== 'GET' || req.path.startsWith('/api')) return next();
      res.sendFile(path.join(distDir, 'index.html'));
    });
    log.info('已挂载前端静态资源 /dist');
  } else {
    log.warn('未找到 /dist，开发模式下请使用 Vite 开发服务器（npm run dev:web）');
  }

  await taskStore.loadAll();
  await blackboard.load();

  const provider = resolveProvider();
  const server = app.listen(config.port, () => {
    log.info(`Creator Agent Studio 服务端已启动: http://127.0.0.1:${config.port}`);
    log.info(
      `LLM 提供方: ${provider.name} / ${provider.model}${provider.simulated ? '（内置离线引擎，无需密钥）' : ''}`,
    );
    log.info(`门禁阈值: 质量分 ≥ ${config.qualityThreshold}｜最大返工 ${config.maxRevisions} 轮｜Turn Budget ${config.turnBudget}`);
  });

  const shutdown = async (signal: string): Promise<void> => {
    log.info(`收到 ${signal}，正在优雅退出…`);
    server.close();
    await taskStore.flushAll();
    await blackboard.flush();
    log.info('数据已保存，进程退出');
    process.exit(0);
  };

  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('unhandledRejection', (reason) => log.error('未处理的 Promise 拒绝', reason));
}

void bootstrap().catch((error: unknown) => {
  log.error('服务启动失败', error);
  process.exit(1);
});
