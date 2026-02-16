import { randomUUID } from 'node:crypto';
import type {
  ExecutionPort,
  SubmitSwapRequest,
  SwapConfirmation,
  SwapSubmission
} from '../../app/ports/execution_port';
import type { LoggerPort } from '../../app/ports/logger_port';
import type { JupiterQuoteClient } from './jupiter_quote_client';

const USDC_ATOMIC_MULTIPLIER = 1_000_000;
const SOL_ATOMIC_MULTIPLIER = 1_000_000_000;

export class PaperExecutionAdapter implements ExecutionPort {
  constructor(
    private readonly quoteClient: JupiterQuoteClient,
    private readonly logger: LoggerPort
  ) {}

  async submitSwap(request: SubmitSwapRequest): Promise<SwapSubmission> {
    const quote = await this.quoteClient.fetchQuote(request);
    const spentQuoteUsdc =
      request.side === 'BUY_SOL_WITH_USDC'
        ? Number(quote.inAmountAtomic) / USDC_ATOMIC_MULTIPLIER
        : Number(quote.outAmountAtomic) / USDC_ATOMIC_MULTIPLIER;
    const filledBaseSol =
      request.side === 'BUY_SOL_WITH_USDC'
        ? Number(quote.outAmountAtomic) / SOL_ATOMIC_MULTIPLIER
        : Number(quote.inAmountAtomic) / SOL_ATOMIC_MULTIPLIER;
    const avgFillPrice = filledBaseSol > 0 ? spentQuoteUsdc / filledBaseSol : 0;
    const paperSignature = `PAPER_${randomUUID()}`;

    this.logger.info('paper execution simulated', {
      tx_signature: paperSignature,
      side: request.side,
      spent_quote_usdc: spentQuoteUsdc,
      filled_base_sol: filledBaseSol,
      avg_fill_price: avgFillPrice
    });

    return {
      txSignature: paperSignature,
      inAmountAtomic: quote.inAmountAtomic,
      outAmountAtomic: quote.outAmountAtomic,
      order: {
        tx_signature: paperSignature
      },
      result: {
        status: 'SIMULATED',
        avg_fill_price: avgFillPrice,
        spent_quote_usdc: spentQuoteUsdc,
        filled_base_sol: filledBaseSol
      }
    };
  }

  confirmSwap(txSignature: string, timeoutMs: number): Promise<SwapConfirmation> {
    void txSignature;
    void timeoutMs;

    return Promise.resolve({
      confirmed: true
    });
  }
}
