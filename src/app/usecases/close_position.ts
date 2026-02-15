import type { ExecutionPort } from '../ports/execution_port';
import type { LockPort } from '../ports/lock_port';
import type { LoggerPort } from '../ports/logger_port';
import type { PersistencePort } from '../ports/persistence_port';
import { assertTradeStateTransition, type TradeState } from '../../domain/model/trade_state';
import type { BotConfig, CloseReason, TradeRecord } from '../../domain/model/types';
import { nowIso, stripUndefined, toErrorMessage } from './usecase_utils';

const SOL_ATOMIC_MULTIPLIER = 1_000_000_000;
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
    currentState = nextState;
    trade.state = nextState;
    trade.updated_at = nowIso();

    await persistence.updateTrade(
      trade.trade_id,
      stripUndefined({
        state: trade.state,
        execution: trade.execution,
        position: trade.position,
        close_reason: trade.close_reason,
        updated_at: trade.updated_at
      })
    );
  };

  const amountAtomic = BigInt(Math.round(trade.position.quantity_sol * SOL_ATOMIC_MULTIPLIER));
  if (amountAtomic <= 0n) {
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
    await moveState('SUBMITTED');

    await lock.setInflightTx(submission.txSignature, TX_INFLIGHT_TTL_SECONDS);

    const confirmation = await execution.confirmSwap(submission.txSignature, TX_CONFIRM_TIMEOUT_MS);
    await lock.clearInflightTx(submission.txSignature);

    if (!confirmation.confirmed) {
      trade.execution.exit_error = confirmation.error ?? 'unknown confirmation error';
      await moveState('FAILED');

      return {
        status: 'FAILED',
        tradeId: trade.trade_id,
        summary: `FAILED: exit tx not confirmed (${trade.execution.exit_error})`
      };
    }

    trade.position.status = 'CLOSED';
    trade.position.exit_price = closePrice;
    trade.position.exit_time_iso = nowIso();
    trade.close_reason = closeReason;

    await moveState('CLOSED');

    return {
      status: 'CLOSED',
      tradeId: trade.trade_id,
      summary: `CLOSED: reason=${closeReason}, tx=${submission.txSignature}`
    };
  } catch (error) {
    const errorMessage = toErrorMessage(error);
    logger.error('close_position failed', { tradeId: trade.trade_id, error: errorMessage });

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
