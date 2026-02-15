export function nowIso(): string {
  return new Date().toISOString();
}

export function toErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

export function stripUndefined<T extends Record<string, unknown>>(value: T): Partial<T> {
  const entries = Object.entries(value).filter(([, entry]) => entry !== undefined);
  return Object.fromEntries(entries) as Partial<T>;
}
