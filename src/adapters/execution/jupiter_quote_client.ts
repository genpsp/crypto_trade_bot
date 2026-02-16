import type { SubmitSwapRequest, SwapSide } from '../../app/ports/execution_port';

const QUOTE_API_URL = 'https://lite-api.jup.ag/swap/v1/quote';
const SOL_MINT = 'So11111111111111111111111111111111111111112';
const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';

interface JupiterQuoteAmounts {
  inAmount: string;
  outAmount: string;
}

function getMints(side: SwapSide): { inputMint: string; outputMint: string } {
  if (side === 'BUY_SOL_WITH_USDC') {
    return {
      inputMint: USDC_MINT,
      outputMint: SOL_MINT
    };
  }

  return {
    inputMint: SOL_MINT,
    outputMint: USDC_MINT
  };
}

export interface JupiterQuote {
  raw: Record<string, unknown>;
  inAmountAtomic: bigint;
  outAmountAtomic: bigint;
}

function formatFetchError(error: unknown): string {
  if (!(error instanceof Error)) {
    return String(error);
  }

  const causeValue = (error as Error & { cause?: unknown }).cause;
  let causeMessage = '';
  if (causeValue instanceof Error) {
    causeMessage = causeValue.message;
  } else if (
    typeof causeValue === 'string' ||
    typeof causeValue === 'number' ||
    typeof causeValue === 'boolean' ||
    typeof causeValue === 'bigint'
  ) {
    causeMessage = String(causeValue);
  } else if (causeValue !== undefined) {
    causeMessage = JSON.stringify(causeValue);
  }

  return causeMessage ? `${error.message} (cause=${causeMessage})` : error.message;
}

export class JupiterQuoteClient {
  async fetchQuote(request: SubmitSwapRequest): Promise<JupiterQuote> {
    const { inputMint, outputMint } = getMints(request.side);
    const url = new URL(QUOTE_API_URL);
    url.searchParams.set('inputMint', inputMint);
    url.searchParams.set('outputMint', outputMint);
    url.searchParams.set('amount', request.amountAtomic.toString());
    url.searchParams.set('slippageBps', String(request.slippageBps));
    url.searchParams.set('onlyDirectRoutes', String(request.onlyDirectRoutes));

    let response: Response;
    try {
      response = await fetch(url.toString());
    } catch (error) {
      throw new Error(`Jupiter quote request failed: ${formatFetchError(error)}`);
    }

    if (!response.ok) {
      throw new Error(`Jupiter quote failed: HTTP ${response.status}`);
    }

    const payload = (await response.json()) as Record<string, unknown>;
    const inAmount = payload.inAmount;
    const outAmount = payload.outAmount;
    if (typeof inAmount !== 'string' || typeof outAmount !== 'string') {
      throw new Error('Jupiter quote payload is missing inAmount/outAmount');
    }

    const amounts: JupiterQuoteAmounts = {
      inAmount,
      outAmount
    };

    return {
      raw: payload,
      inAmountAtomic: BigInt(amounts.inAmount),
      outAmountAtomic: BigInt(amounts.outAmount)
    };
  }
}
