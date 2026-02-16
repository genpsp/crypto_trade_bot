import type { TradeOrderSnapshot, TradeResultSnapshot } from '../../domain/model/types';

export type SwapSide = 'BUY_SOL_WITH_USDC' | 'SELL_SOL_FOR_USDC';

export interface SubmitSwapRequest {
  side: SwapSide;
  amountAtomic: bigint;
  slippageBps: number;
  onlyDirectRoutes: boolean;
}

export interface SwapSubmission {
  txSignature: string;
  inAmountAtomic: bigint;
  outAmountAtomic: bigint;
  order?: TradeOrderSnapshot;
  result?: TradeResultSnapshot;
}

export interface SwapConfirmation {
  confirmed: boolean;
  error?: string;
}

export interface ExecutionPort {
  submitSwap(request: SubmitSwapRequest): Promise<SwapSubmission>;
  confirmSwap(txSignature: string, timeoutMs: number): Promise<SwapConfirmation>;
}
