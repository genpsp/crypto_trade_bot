import type { ExecutionPort } from '../ports/execution_port';
import type { LockPort } from '../ports/lock_port';
import type { LoggerPort } from '../ports/logger_port';
import type { MarketDataPort } from '../ports/market_data_port';
import type { PersistencePort } from '../ports/persistence_port';
import { evaluateEmaTrendPullbackV0 } from '../../domain/strategy/ema_trend_pullback_v0';
import type { RunRecord } from '../../domain/model/types';
import { buildRunId, getLastClosed4hBarClose, getUtcDayRange } from '../../domain/utils/time';
import { closePosition } from './close_position';
import { openPosition } from './open_position';
import { toErrorMessage } from './usecase_utils';

const RUN_LOCK_TTL_SECONDS = 240;
const BAR_IDEM_TTL_SECONDS = 12 * 60 * 60;
const OHLCV_LIMIT = 300;

export interface Run4hCycleDependencies {
  execution: ExecutionPort;
  lock: LockPort;
  logger: LoggerPort;
  marketData: MarketDataPort;
  persistence: PersistencePort;
  nowProvider?: () => Date;
}

export async function run4hCycle(dependencies: Run4hCycleDependencies): Promise<RunRecord> {
  const { execution, lock, logger, marketData, persistence, nowProvider } = dependencies;

  const runAt = nowProvider?.() ?? new Date();
  const barCloseTime = getLastClosed4hBarClose(runAt);
  const barCloseTimeIso = barCloseTime.toISOString();

  const run: RunRecord = {
    run_id: buildRunId(barCloseTimeIso, runAt),
    bar_close_time_iso: barCloseTimeIso,
    executed_at_iso: runAt.toISOString(),
    result: 'FAILED',
    summary: 'FAILED: run initialization'
  };

  const locked = await lock.acquireRunnerLock(RUN_LOCK_TTL_SECONDS);
  if (!locked) {
    run.result = 'SKIPPED';
    run.summary = 'SKIPPED: lock:runner already acquired by another process';
    await persistence.saveRun(run);
    return run;
  }

  try {
    const marked = await lock.markBarProcessed(barCloseTimeIso, BAR_IDEM_TTL_SECONDS);
    if (!marked) {
      run.result = 'SKIPPED';
      run.summary = 'SKIPPED: idem key already exists for this bar';
      return run;
    }

    const config = await persistence.getCurrentConfig();
    run.config_version = config.meta.config_version;

    if (!config.enabled) {
      run.result = 'SKIPPED';
      run.summary = 'SKIPPED: config/current.enabled is false';
      return run;
    }

    const bars = await marketData.fetch4hBars(config.pair, OHLCV_LIMIT);
    const closedBars = bars.filter((bar) => bar.closeTime.getTime() <= barCloseTime.getTime());
    const latestClosedBar = closedBars.at(-1);

    if (!latestClosedBar) {
      run.result = 'FAILED';
      run.summary = 'FAILED: no closed bars available';
      return run;
    }

    if (latestClosedBar.closeTime.getTime() !== barCloseTime.getTime()) {
      run.result = 'FAILED';
      run.summary = 'FAILED: market bar close does not match expected 4h close';
      run.reason = `EXPECTED_${barCloseTimeIso}_GOT_${latestClosedBar.closeTime.toISOString()}`;
      return run;
    }

    const openTrade = await persistence.findOpenTrade(config.pair);
    if (openTrade) {
      run.trade_id = openTrade.trade_id;

      if (latestClosedBar.close >= openTrade.position.take_profit_price) {
        const closed = await closePosition(
          {
            execution,
            lock,
            logger,
            persistence
          },
          {
            config,
            trade: openTrade,
            closeReason: 'TAKE_PROFIT',
            closePrice: latestClosedBar.close
          }
        );

        run.result = closed.status === 'CLOSED' ? 'CLOSED' : 'FAILED';
        run.summary = closed.summary;
        return run;
      }

      if (latestClosedBar.close <= openTrade.position.stop_price) {
        const closed = await closePosition(
          {
            execution,
            lock,
            logger,
            persistence
          },
          {
            config,
            trade: openTrade,
            closeReason: 'STOP_LOSS',
            closePrice: latestClosedBar.close
          }
        );

        run.result = closed.status === 'CLOSED' ? 'CLOSED' : 'FAILED';
        run.summary = closed.summary;
        return run;
      }

      run.result = 'HOLD';
      run.summary = 'HOLD: open position exists and no exit trigger fired on this bar';
      return run;
    }

    const { dayStartIso, dayEndIso } = getUtcDayRange(barCloseTime);
    const tradesToday = await persistence.countTradesForUtcDay(config.pair, dayStartIso, dayEndIso);
    if (tradesToday >= config.risk.max_trades_per_day) {
      run.result = 'SKIPPED';
      run.summary = 'SKIPPED: max_trades_per_day reached';
      run.reason = `TRADES_TODAY_${tradesToday}`;
      return run;
    }

    const decision = evaluateEmaTrendPullbackV0({
      bars: closedBars,
      strategy: config.strategy,
      exit: config.exit,
      execution: config.execution
    });

    if (decision.type === 'NO_SIGNAL') {
      run.result = 'NO_SIGNAL';
      run.summary = decision.summary;
      run.reason = decision.reason;
      return run;
    }

    const opened = await openPosition(
      {
        execution,
        lock,
        logger,
        persistence
      },
      {
        config,
        signal: decision,
        barCloseTimeIso
      }
    );

    run.trade_id = opened.tradeId;
    run.result = opened.status === 'OPENED' ? 'OPENED' : 'FAILED';
    run.summary = opened.summary;

    return run;
  } catch (error) {
    const errorMessage = toErrorMessage(error);
    run.result = 'FAILED';
    run.summary = 'FAILED: unhandled run_4h_cycle error';
    run.reason = errorMessage;
    logger.error('run_4h_cycle unhandled error', { error: errorMessage });

    return run;
  } finally {
    try {
      await persistence.saveRun(run);
    } catch (saveError) {
      logger.error('failed to save runs/{run_id}', {
        error: toErrorMessage(saveError),
        run_id: run.run_id
      });
    }

    await lock.releaseRunnerLock();
  }
}
