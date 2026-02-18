import type { SignalTimeframe } from '../model/types';

const TIMEFRAME_TO_MS: Record<SignalTimeframe, number> = {
  '2h': 2 * 60 * 60 * 1000,
  '4h': 4 * 60 * 60 * 1000
};

export function getBarDurationMs(timeframe: SignalTimeframe): number {
  return TIMEFRAME_TO_MS[timeframe];
}

export function getLastClosedBarClose(now: Date, timeframe: SignalTimeframe): Date {
  const ms = now.getTime();
  const durationMs = getBarDurationMs(timeframe);
  const closeMs = Math.floor(ms / durationMs) * durationMs;
  return new Date(closeMs);
}

export function getUtcDayRange(target: Date): { dayStartIso: string; dayEndIso: string } {
  const dayStart = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth(), target.getUTCDate(), 0, 0, 0, 0)
  );
  const dayEnd = new Date(dayStart.getTime() + 24 * 60 * 60 * 1000 - 1);

  return {
    dayStartIso: dayStart.toISOString(),
    dayEndIso: dayEnd.toISOString()
  };
}

export function buildTradeId(barCloseTimeIso: string): string {
  return `${barCloseTimeIso}_SOLUSDC_LONG`;
}

export function buildRunId(barCloseTimeIso: string, runAt: Date): string {
  const safeBar = barCloseTimeIso.replace(/[:.]/g, '-');
  return `${safeBar}_${runAt.getTime()}`;
}
