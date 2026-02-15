import type { LoggerPort } from '../../app/ports/logger_port';

function formatContext(context?: Record<string, unknown>): string {
  if (!context || Object.keys(context).length === 0) {
    return '';
  }

  return ` ${JSON.stringify(context)}`;
}

export function createLogger(component = 'bot'): LoggerPort {
  return {
    info(message, context) {
      console.log(`[INFO] [${component}] ${message}${formatContext(context)}`);
    },
    warn(message, context) {
      console.warn(`[WARN] [${component}] ${message}${formatContext(context)}`);
    },
    error(message, context) {
      console.error(`[ERROR] [${component}] ${message}${formatContext(context)}`);
    }
  };
}
