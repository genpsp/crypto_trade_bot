import type { ExecutionPort } from '../ports/execution_port';
import type { LockPort } from '../ports/lock_port';
import type { LoggerPort } from '../ports/logger_port';
import type { MarketDataPort } from '../ports/market_data_port';
import type { PersistencePort } from '../ports/persistence_port';
import { evaluateEmaTrendPullbackV0 } from '../../domain/strategy/ema_trend_pullback_v0';
import type { RunRecord } from '../../domain/model/types';
import { buildRunId, getLastClosedBarClose, getUtcDayRange } from '../../domain/utils/time';
import { roundTo } from '../../domain/utils/math';
import { closePosition } from './close_position';
import { openPosition } from './open_position';
import { toErrorMessage } from './usecase_utils';

const RUN_LOCK_TTL_SECONDS = 240;
const ENTRY_IDEM_TTL_SECONDS = 12 * 60 * 60;
const OHLCV_LIMIT = 300;

export interface RunCycleDependencies {
  execution: ExecutionPort;
  lock: LockPort;
  logger: LoggerPort;
  marketData: MarketDataPort;
  persistence: PersistencePort;
  nowProvider?: () => Date;
}

export async function runCycle(dependencies: RunCycleDependencies): Promise<RunRecord> {
  const { execution, lock, logger, marketData, persistence, nowProvider } = dependencies;

  const runAt = nowProvider?.() ?? new Date();
  const runAtIso = runAt.toISOString();
  const provisionalBarCloseTimeIso = runAtIso;

  const run: RunRecord = {
    run_id: buildRunId(provisionalBarCloseTimeIso, runAt),
    bar_close_time_iso: provisionalBarCloseTimeIso,
    executed_at_iso: runAtIso,
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
    const config = await persistence.getCurrentConfig();
    const timeframe = config.signal_timeframe;
    const barCloseTime = getLastClosedBarClose(runAt, timeframe);
    const barCloseTimeIso = barCloseTime.toISOString();
    run.run_id = buildRunId(barCloseTimeIso, runAt);
    run.bar_close_time_iso = barCloseTimeIso;
    run.config_version = config.meta.config_version;

    if (!config.enabled) {
      run.result = 'SKIPPED';
      run.summary = 'SKIPPED: config/current.enabled is false';
      return run;
    }

    const openTrade = await persistence.findOpenTrade(config.pair);

    if (openTrade) {
      run.trade_id = openTrade.trade_id;
      const markPriceFromExecution = execution.getMarkPrice
        ? await execution.getMarkPrice(config.pair)
        : undefined;
      const markPriceFromBars =
        markPriceFromExecution === undefined
          ? (await marketData.fetchBars(config.pair, timeframe, 1)).at(-1)?.close
          : undefined;
      const markPrice = markPriceFromExecution ?? markPriceFromBars;
      if (markPrice === undefined) {
        run.result = 'FAILED';
        run.summary = 'FAILED: no mark price available';
        return run;
      }
      const triggerReason =
        markPrice >= openTrade.position.take_profit_price
          ? 'TAKE_PROFIT'
          : markPrice <= openTrade.position.stop_price
            ? 'STOP_LOSS'
            : 'NONE';
      logger.info('exit check', {
        markPrice: roundTo(markPrice, 6),
        stop: roundTo(openTrade.position.stop_price, 6),
        tp: roundTo(openTrade.position.take_profit_price, 6),
        triggerReason
      });

      if (triggerReason === 'TAKE_PROFIT') {
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
            closePrice: markPrice
          }
        );

        run.result = closed.status === 'CLOSED' ? 'CLOSED' : 'FAILED';
        run.summary = closed.summary;
        return run;
      }

      if (triggerReason === 'STOP_LOSS') {
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
            closePrice: markPrice
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

    const alreadyJudged = await lock.hasEntryAttempt(barCloseTimeIso);
    if (alreadyJudged) {
      run.result = 'SKIPPED_ENTRY';
      run.summary = 'SKIPPED_ENTRY: entry already evaluated for this bar';
      return run;
    }

    const bars = await marketData.fetchBars(config.pair, timeframe, OHLCV_LIMIT);
    const closedBars = bars.filter((bar) => bar.closeTime.getTime() <= barCloseTime.getTime());
    const latestClosedBar = closedBars.at(-1);

    if (!latestClosedBar) {
      run.result = 'FAILED';
      run.summary = 'FAILED: no closed bars available';
      return run;
    }

    if (latestClosedBar.closeTime.getTime() !== barCloseTime.getTime()) {
      run.result = 'FAILED';
      run.summary = `FAILED: market bar close does not match expected ${timeframe} close`;
      run.reason = `EXPECTED_${barCloseTimeIso}_GOT_${latestClosedBar.closeTime.toISOString()}`;
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
      risk: config.risk,
      exit: config.exit,
      execution: config.execution
    });
    logger.info('strategy evaluation', {
      bar_close_time_iso: barCloseTimeIso,
      decision_type: decision.type,
      summary: decision.summary,
      reason: decision.type === 'NO_SIGNAL' ? decision.reason : undefined,
      ema_fast: decision.ema_fast,
      ema_slow: decision.ema_slow,
      entry_price: decision.type === 'ENTER' ? decision.entry_price : undefined,
      stop_price: decision.type === 'ENTER' ? decision.stop_price : undefined,
      take_profit_price: decision.type === 'ENTER' ? decision.take_profit_price : undefined,
      diagnostics: decision.diagnostics
    });

    if (decision.type === 'NO_SIGNAL') {
      await lock.markEntryAttempt(barCloseTimeIso, ENTRY_IDEM_TTL_SECONDS);
      run.result = 'NO_SIGNAL';
      run.summary = decision.summary;
      run.reason = decision.reason;
      return run;
    }

    const marked = await lock.markEntryAttempt(barCloseTimeIso, ENTRY_IDEM_TTL_SECONDS);
    if (!marked) {
      run.result = 'SKIPPED_ENTRY';
      run.summary = 'SKIPPED_ENTRY: idem entry key already exists for this bar';
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
    run.summary = 'FAILED: unhandled run_cycle error';
    run.reason = errorMessage;
    logger.error('run_cycle unhandled error', { error: errorMessage });

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
