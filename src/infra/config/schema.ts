import { z } from 'zod';

export const configSchema = z
  .object({
    enabled: z.boolean(),
    network: z.literal('mainnet-beta'),
    pair: z.literal('SOL/USDC'),
    direction: z.literal('LONG_ONLY'),
    signal_timeframe: z.literal('4h'),
    strategy: z.object({
      name: z.literal('ema_trend_pullback_v0'),
      ema_fast_period: z.number().int().positive(),
      ema_slow_period: z.number().int().positive(),
      swing_low_lookback_bars: z.number().int().positive(),
      entry: z.literal('ON_4H_CLOSE')
    }),
    risk: z.object({
      max_loss_per_trade_pct: z.number().positive(),
      max_trades_per_day: z.number().int().positive()
    }),
    execution: z.object({
      mode: z.enum(['PAPER', 'LIVE']).default('PAPER'),
      swap_provider: z.literal('JUPITER'),
      slippage_bps: z.number().int().positive(),
      min_notional_usdc: z.number().positive(),
      only_direct_routes: z.boolean()
    }),
    exit: z.object({
      stop: z.literal('SWING_LOW'),
      take_profit_r_multiple: z.number().positive()
    }),
    meta: z.object({
      config_version: z.number().int().positive(),
      note: z.string().min(1)
    })
  })
  .strict();
