import type { ExecutionPort } from '../ports/execution_port';
import type { LockPort } from '../ports/lock_port';
import type { LoggerPort } from '../ports/logger_port';
import type { PersistencePort } from '../ports/persistence_port';
import { assertTradeStateTransition, type TradeState } from '../../domain/model/trade_state';
import type {
  BotConfig,
  EntrySignalDecision,
  TradeExecutionSnapshot,
  TradePositionSnapshot,
  TradeRecord
} from '../../domain/model/types';
import { buildTradeId } from '../../domain/utils/time';
import { roundTo } from '../../domain/utils/math';
import { nowIso, stripUndefined, toErrorMessage } from './usecase_utils';

const USDC_ATOMIC_MULTIPLIER = 1_000_000;
const SOL_ATOMIC_MULTIPLIER = 1_000_000_000;
const TX_CONFIRM_TIMEOUT_MS = 75_000;
const TX_INFLIGHT_TTL_SECONDS = 180;

export interface OpenPositionInput {
  config: BotConfig;
  signal: EntrySignalDecision;
  barCloseTimeIso: string;
}

export interface OpenPositionResult {
  status: 'OPENED' | 'FAILED';
  tradeId: string;
  summary: string;
}

export interface OpenPositionDependencies {
  execution: ExecutionPort;
  lock: LockPort;
  logger: LoggerPort;
  persistence: PersistencePort;
}

export async function openPosition(
  dependencies: OpenPositionDependencies,
  input: OpenPositionInput
): Promise<OpenPositionResult> {
  const { execution, lock, logger, persistence } = dependencies;
  const { config, signal, barCloseTimeIso } = input;

  const tradeId = buildTradeId(barCloseTimeIso);
  const now = nowIso();

  const executionSnapshot: TradeExecutionSnapshot = {};
  const positionSnapshot: TradePositionSnapshot = {
    status: 'OPEN',
    quantity_sol: 0,
    entry_price: signal.entry_price,
    stop_price: signal.stop_price,
    take_profit_price: signal.take_profit_price
  };

  const trade: TradeRecord = {
    trade_id: tradeId,
    bar_close_time_iso: barCloseTimeIso,
    pair: config.pair,
    direction: config.direction,
    state: 'CREATED',
    config_version: config.meta.config_version,
    signal: {
      summary: signal.summary,
      bar_close_time_iso: barCloseTimeIso,
      ema_fast: signal.ema_fast,
      ema_slow: signal.ema_slow
    },
    plan: {
      summary: `Buy SOL with ${config.execution.min_notional_usdc} USDC, stop=${roundTo(
        signal.stop_price,
        4
      )}, tp=${roundTo(signal.take_profit_price, 4)}`,
      notional_usdc: config.execution.min_notional_usdc,
      entry_price: signal.entry_price,
      stop_price: signal.stop_price,
      take_profit_price: signal.take_profit_price,
      r_multiple: config.exit.take_profit_r_multiple
    },
    execution: executionSnapshot,
    position: positionSnapshot,
    created_at: now,
    updated_at: now
  };

  await persistence.createTrade(trade);
  let currentState: TradeState = trade.state;

  const moveState = async (nextState: TradeState): Promise<void> => {
    assertTradeStateTransition(currentState, nextState);
    currentState = nextState;
    trade.state = nextState;
    trade.updated_at = nowIso();

    await persistence.updateTrade(
      trade.trade_id,
      stripUndefined({
        state: trade.state,
        execution: trade.execution,
        position: trade.position,
        updated_at: trade.updated_at
      })
    );
  };

  const notionalUsdc = config.execution.min_notional_usdc;
  if (notionalUsdc <= 0) {
    trade.execution.entry_error = 'min_notional_usdc must be > 0';
    await moveState('FAILED');
    return {
      status: 'FAILED',
      tradeId,
      summary: 'FAILED: invalid min_notional_usdc'
    };
  }

  const amountAtomic = BigInt(Math.round(notionalUsdc * USDC_ATOMIC_MULTIPLIER));

  try {
    const submission = await execution.submitSwap({
      side: 'BUY_SOL_WITH_USDC',
      amountAtomic,
      slippageBps: config.execution.slippage_bps,
      onlyDirectRoutes: config.execution.only_direct_routes
    });

    trade.execution.entry_tx_signature = submission.txSignature;
    await moveState('SUBMITTED');

    await lock.setInflightTx(submission.txSignature, TX_INFLIGHT_TTL_SECONDS);

    const confirmation = await execution.confirmSwap(submission.txSignature, TX_CONFIRM_TIMEOUT_MS);
    await lock.clearInflightTx(submission.txSignature);

    if (!confirmation.confirmed) {
      trade.execution.entry_error = confirmation.error ?? 'unknown confirmation error';
      await moveState('FAILED');

      return {
        status: 'FAILED',
        tradeId,
        summary: `FAILED: entry tx not confirmed (${trade.execution.entry_error})`
      };
    }

    const receivedSol = Number(submission.outAmountAtomic) / SOL_ATOMIC_MULTIPLIER;
    const resolvedEntryPrice = receivedSol > 0 ? notionalUsdc / receivedSol : signal.entry_price;

    trade.position.quantity_sol = roundTo(receivedSol, 9);
    trade.position.entry_price = roundTo(resolvedEntryPrice, 6);
    trade.position.entry_time_iso = nowIso();

    await moveState('CONFIRMED');

    return {
      status: 'OPENED',
      tradeId,
      summary: `OPENED: tx=${submission.txSignature}, qty=${trade.position.quantity_sol} SOL`
    };
  } catch (error) {
    const errorMessage = toErrorMessage(error);
    logger.error('open_position failed', { tradeId, error: errorMessage });

    trade.execution.entry_error = errorMessage;

    try {
      await moveState('FAILED');
    } catch (stateError) {
      logger.error('open_position state transition failed', {
        tradeId,
        error: toErrorMessage(stateError)
      });
    }

    return {
      status: 'FAILED',
      tradeId,
      summary: `FAILED: ${errorMessage}`
    };
  }
}
