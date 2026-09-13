type Level = 'debug' | 'info' | 'warn' | 'error';

const COLOR: Record<Level, string> = {
  debug: '\u001b[90m',
  info: '\u001b[36m',
  warn: '\u001b[33m',
  error: '\u001b[31m',
};
const RESET = '\u001b[0m';

function ts(): string {
  return new Date().toISOString().slice(11, 23);
}

function write(level: Level, scope: string, message: string, extra?: unknown): void {
  const head = `${COLOR[level]}[${ts()}] ${level.toUpperCase().padEnd(5)} [${scope}]${RESET}`;
  if (extra === undefined) {
    console.log(`${head} ${message}`);
  } else {
    console.log(`${head} ${message}`, extra);
  }
}

export function createLogger(scope: string) {
  return {
    debug: (msg: string, extra?: unknown) => write('debug', scope, msg, extra),
    info: (msg: string, extra?: unknown) => write('info', scope, msg, extra),
    warn: (msg: string, extra?: unknown) => write('warn', scope, msg, extra),
    error: (msg: string, extra?: unknown) => write('error', scope, msg, extra),
  };
}

export const logger = createLogger('app');
