import type { ExecutionPort } from '../ports/execution_port';
import type { LockPort } from '../ports/lock_port';
import type { LoggerPort } from '../ports/logger_port';
import type { PersistencePort } from '../ports/persistence_port';
import { roundTo } from '../../domain/utils/math';
import { assertTradeStateTransition, type TradeState } from '../../domain/model/trade_state';
import type { BotConfig, CloseReason, TradeRecord } from '../../domain/model/types';
import { nowIso, stripUndefined, toErrorMessage } from './usecase_utils';

const SOL_ATOMIC_MULTIPLIER = 1_000_000_000;
const USDC_ATOMIC_MULTIPLIER = 1_000_000;
const TX_CONFIRM_TIMEOUT_MS = 75_000;
const TX_INFLIGHT_TTL_SECONDS = 180;

export interface ClosePositionInput {
  config: BotConfig;
  trade: TradeRecord;
  closeReason: CloseReason;
  closePrice: number;
}

export interface ClosePositionResult {
  status: 'CLOSED' | 'FAILED';
  tradeId: string;
  summary: string;
}

export interface ClosePositionDependencies {
  execution: ExecutionPort;
  lock: LockPort;
  logger: LoggerPort;
  persistence: PersistencePort;
}

export async function closePosition(
  dependencies: ClosePositionDependencies,
  input: ClosePositionInput
): Promise<ClosePositionResult> {
  const { execution, lock, logger, persistence } = dependencies;
  const { config, trade, closeReason, closePrice } = input;

  if (trade.state !== 'CONFIRMED') {
    return {
      status: 'FAILED',
      tradeId: trade.trade_id,
      summary: `FAILED: trade state is ${trade.state}, expected CONFIRMED`
    };
  }

  let currentState: TradeState = trade.state;

  const moveState = async (nextState: TradeState): Promise<void> => {
    assertTradeStateTransition(currentState, nextState);
    const nextUpdatedAt = nowIso();

    await persistence.updateTrade(
      trade.trade_id,
      stripUndefined({
        state: nextState,
        execution: trade.execution,
        position: trade.position,
        close_reason: trade.close_reason,
        updated_at: nextUpdatedAt
      })
    );

    currentState = nextState;
    trade.state = nextState;
    trade.updated_at = nextUpdatedAt;
  };

  const amountAtomic = BigInt(Math.floor(trade.position.quantity_sol * SOL_ATOMIC_MULTIPLIER));
  if (amountAtomic <= 0n) {
    trade.execution.exit_submission_state = 'FAILED';
    trade.execution.exit_error = 'position quantity is 0';
    await moveState('FAILED');

    return {
      status: 'FAILED',
      tradeId: trade.trade_id,
      summary: 'FAILED: position quantity is 0'
    };
  }

  try {
    const submission = await execution.submitSwap({
      side: 'SELL_SOL_FOR_USDC',
      amountAtomic,
      slippageBps: config.execution.slippage_bps,
      onlyDirectRoutes: config.execution.only_direct_routes
    });

    trade.execution.exit_tx_signature = submission.txSignature;
    if (submission.order) {
      trade.execution.exit_order = submission.order;
    }
    if (submission.result) {
      trade.execution.exit_result = submission.result;
    }
    trade.execution.exit_submission_state = 'SUBMITTED';
    trade.updated_at = nowIso();
    await persistence.updateTrade(
      trade.trade_id,
      stripUndefined({
        execution: trade.execution,
        updated_at: trade.updated_at
      })
    );

    await lock.setInflightTx(submission.txSignature, TX_INFLIGHT_TTL_SECONDS);

    const confirmation = await execution.confirmSwap(submission.txSignature, TX_CONFIRM_TIMEOUT_MS);
    await lock.clearInflightTx(submission.txSignature);

    if (!confirmation.confirmed) {
      trade.execution.exit_submission_state = 'FAILED';
      trade.execution.exit_error = confirmation.error ?? 'unknown confirmation error';
      await moveState('FAILED');

      return {
        status: 'FAILED',
        tradeId: trade.trade_id,
        summary: `FAILED: exit tx not confirmed (${trade.execution.exit_error})`
      };
    }

    const inputSol = Number(submission.inAmountAtomic) / SOL_ATOMIC_MULTIPLIER;
    const outputUsdc = Number(submission.outAmountAtomic) / USDC_ATOMIC_MULTIPLIER;
    const fallbackExitPrice = inputSol > 0 ? outputUsdc / inputSol : closePrice;
    const resolvedExitPrice = submission.result?.avg_fill_price ?? fallbackExitPrice;

    const previousPositionSnapshot = { ...trade.position };
    const previousCloseReason = trade.close_reason;
    trade.execution.exit_submission_state = 'CONFIRMED';
    trade.position.status = 'CLOSED';
    trade.position.exit_price = roundTo(resolvedExitPrice, 6);
    trade.position.exit_trigger_price = roundTo(closePrice, 6);
    trade.position.exit_time_iso = nowIso();
    trade.close_reason = closeReason;
    try {
      await moveState('CLOSED');
    } catch (closeCommitError) {
      trade.position = previousPositionSnapshot;
      trade.close_reason = previousCloseReason;
      throw closeCommitError;
    }

    return {
      status: 'CLOSED',
      tradeId: trade.trade_id,
      summary: `CLOSED: reason=${closeReason}, tx=${submission.txSignature}, fill=${roundTo(
        trade.position.exit_price,
        4
      )}, trigger=${roundTo(closePrice, 4)}`
    };
  } catch (error) {
    const errorMessage = toErrorMessage(error);
    logger.error('close_position failed', { tradeId: trade.trade_id, error: errorMessage });

    trade.execution.exit_submission_state = 'FAILED';
    trade.execution.exit_error = errorMessage;

    try {
      await moveState('FAILED');
    } catch (stateError) {
      logger.error('close_position state transition failed', {
        tradeId: trade.trade_id,
        error: toErrorMessage(stateError)
      });
    }

    return {
      status: 'FAILED',
      tradeId: trade.trade_id,
      summary: `FAILED: ${errorMessage}`
    };
  }
}
