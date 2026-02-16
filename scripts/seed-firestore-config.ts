import 'dotenv/config';
import { Firestore } from '@google-cloud/firestore';
import { configSchema } from '../src/infra/config/schema';

type Mode = 'PAPER' | 'LIVE';

interface CliArgs {
  mode: Mode;
}

function parseArgs(argv: string[]): CliArgs {
  const argMap = new Map<string, string>();

  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];

    if (!key?.startsWith('--') || !value) {
      continue;
    }

    argMap.set(key, value);
  }

  const modeRaw = argMap.get('--mode') ?? 'PAPER';
  if (modeRaw !== 'PAPER' && modeRaw !== 'LIVE') {
    throw new Error('Invalid --mode. Use PAPER or LIVE.');
  }

  return {
    mode: modeRaw
  };
}

function buildDefaultConfig(mode: Mode): unknown {
  return {
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
      mode,
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
      note: 'v0: spot swap only, long only, 4h close entry, TP=2R all, notify=none'
    }
  };
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const credentialsPath = process.env.GOOGLE_APPLICATION_CREDENTIALS;

  if (!credentialsPath) {
    throw new Error('GOOGLE_APPLICATION_CREDENTIALS is required');
  }

  const config = configSchema.parse(buildDefaultConfig(args.mode));
  const firestore = new Firestore({
    keyFilename: credentialsPath
  });

  await firestore.doc('config/current').set(config);
  console.log(`Seeded config/current with execution.mode=${config.execution.mode}`);
}

void main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`[ERROR] seed-firestore-config failed: ${message}`);
  process.exit(1);
});
