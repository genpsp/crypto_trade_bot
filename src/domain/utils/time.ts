const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;

export function getLastClosed4hBarClose(now: Date): Date {
  const ms = now.getTime();
  const closeMs = Math.floor(ms / FOUR_HOURS_MS) * FOUR_HOURS_MS;
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
