import { describe, expect, it } from 'vitest';
import { configSchema } from '../../../src/infra/config/schema';

const baseConfig = {
  enabled: true,
  network: 'mainnet-beta',
  pair: 'SOL/USDC',
  direction: 'LONG_ONLY',
  signal_timeframe: '4h',
  strategy: {
    name: 'ema_trend_pullback_v0',
    ema_fast_period: 20,
    ema_slow_period: 50,
    swing_low_lookback_bars: 12,
    entry: 'ON_4H_CLOSE'
  },
  risk: {
    max_loss_per_trade_pct: 0.5,
    max_trades_per_day: 3
  },
  execution: {
    swap_provider: 'JUPITER',
    slippage_bps: 100,
    min_notional_usdc: 50,
    only_direct_routes: false
  },
  exit: {
    stop: 'SWING_LOW',
    take_profit_r_multiple: 2
  },
  meta: {
    config_version: 1,
    note: 'test'
  }
} as const;

describe('configSchema execution.mode', () => {
  it('defaults execution.mode to PAPER when omitted', () => {
    const parsed = configSchema.parse(baseConfig);
    expect(parsed.execution.mode).toBe('PAPER');
  });

  it('rejects unknown execution.mode', () => {
    const parsed = configSchema.safeParse({
      ...baseConfig,
      execution: {
        ...baseConfig.execution,
        mode: 'STAGING'
      }
    });

    expect(parsed.success).toBe(false);
  });

  it('accepts LIVE mode', () => {
    const parsed = configSchema.parse({
      ...baseConfig,
      execution: {
        ...baseConfig.execution,
        mode: 'LIVE'
      }
    });

    expect(parsed.execution.mode).toBe('LIVE');
  });
});
