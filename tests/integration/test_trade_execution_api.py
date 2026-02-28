from __future__ import annotations

import json
import threading
import unittest
from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from solders.keypair import Keypair

from pybot.adapters.execution.jupiter_quote_client import SOL_MINT, USDC_MINT, JupiterQuoteClient
from pybot.adapters.execution.jupiter_swap import JupiterSwapAdapter
from pybot.adapters.execution.solana_sender import SignatureConfirmation, SolanaSender
from pybot.app.ports.execution_port import SubmitSwapRequest, SwapConfirmation, SwapSubmission
from pybot.app.usecases.close_position import (
    ClosePositionDependencies,
    ClosePositionInput,
    close_position,
)
from pybot.app.usecases.open_position import (
    OpenPositionDependencies,
    OpenPositionInput,
    open_position,
)
from pybot.domain.model.types import BotConfig, EntrySignalDecision, Pair, RunRecord, TradeRecord


class InMemoryLogger:
    def info(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def warn(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context

    def error(self, message: str, context: dict[str, Any] | None = None) -> None:
        _ = message
        _ = context


class InMemoryLock:
    def __init__(self) -> None:
        self.inflight: dict[str, int] = {}
        self.entry: set[str] = set()
        self.locked = False

    def acquire_runner_lock(self, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        if self.locked:
            return False
        self.locked = True
        return True

    def release_runner_lock(self) -> None:
        self.locked = False

    def mark_entry_attempt(self, bar_close_time_iso: str, ttl_seconds: int) -> bool:
        _ = ttl_seconds
        if bar_close_time_iso in self.entry:
            return False
        self.entry.add(bar_close_time_iso)
        return True

    def has_entry_attempt(self, bar_close_time_iso: str) -> bool:
        return bar_close_time_iso in self.entry

    def clear_entry_attempt(self, bar_close_time_iso: str) -> None:
        self.entry.discard(bar_close_time_iso)

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        self.inflight[signature] = ttl_seconds

    def has_inflight_tx(self, signature: str) -> bool:
        return signature in self.inflight

    def clear_inflight_tx(self, signature: str) -> None:
        self.inflight.pop(signature, None)


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
            continue
        dst[key] = deepcopy(value)


class InMemoryPersistence:
    def __init__(self, config: BotConfig):
        self.config = config
        self.trades: dict[str, TradeRecord] = {}
        self.runs: dict[str, RunRecord] = {}

    def get_current_config(self) -> BotConfig:
        return self.config

    def create_trade(self, trade: TradeRecord) -> None:
        self.trades[trade["trade_id"]] = deepcopy(trade)

    def update_trade(self, trade_id: str, updates: dict[str, Any]) -> None:
        current = self.trades.get(trade_id)
        if current is None:
            raise KeyError(f"trade not found: {trade_id}")
        _merge(current, updates)

    def find_open_trade(self, pair: Pair) -> TradeRecord | None:
        candidates = [
            trade
            for trade in self.trades.values()
            if trade.get("pair") == pair and trade.get("state") == "CONFIRMED"
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return candidates[0]

    def count_trades_for_utc_day(self, pair: Pair, day_start_iso: str, day_end_iso: str) -> int:
        _ = pair
        _ = day_start_iso
        _ = day_end_iso
        return 0

    def save_run(self, run: RunRecord) -> None:
        self.runs[run["run_id"]] = deepcopy(run)


class FakeSolanaSender:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.confirmed: list[str] = []
        self._counter = 0

    def get_public_key_base58(self) -> str:
        return str(Keypair().pubkey())

    def send_versioned_transaction_base64(self, serialized_base64: str) -> str:
        self.sent.append(serialized_base64)
        self._counter += 1
        return f"sig-{self._counter}"

    def confirm_signature(self, signature: str, timeout_ms: int) -> SignatureConfirmation:
        _ = timeout_ms
        self.confirmed.append(signature)
        return SignatureConfirmation(confirmed=True)

    def get_spl_token_balance_ui_amount(self, mint: str) -> float:
        _ = mint
        return 50.0

    def get_native_sol_balance_ui_amount(self) -> float:
        return 1.0


@dataclass
class MockServer:
    responder: Callable[
        [str, str, dict[str, list[str]], dict[str, Any] | None],
        tuple[int, dict[str, Any]],
    ]
    httpd: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    requests: list[dict[str, Any]] | None = None

    def __enter__(self) -> "MockServer":
        requests_log: list[dict[str, Any]] = []
        self.requests = requests_log
        responder = self.responder

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                body_raw = b""
                body_json: dict[str, Any] | None = None
                if self.command == "POST":
                    length = int(self.headers.get("Content-Length", "0"))
                    body_raw = self.rfile.read(length) if length > 0 else b""
                    if body_raw:
                        body_json = json.loads(body_raw.decode("utf-8"))
                requests_log.append(
                    {
                        "method": self.command,
                        "path": parsed.path,
                        "query": query,
                        "body_json": body_json,
                    }
                )
                status, payload = responder(self.command, parsed.path, query, body_json)
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                _ = format
                _ = args

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        if self.httpd is None:
            raise RuntimeError("mock server is not started")
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"


def _build_config() -> BotConfig:
    return {
        "enabled": True,
        "network": "mainnet-beta",
        "pair": "SOL/USDC",
        "direction": "LONG",
        "signal_timeframe": "2h",
        "strategy": {
            "name": "ema_trend_pullback_v0",
            "ema_fast_period": 12,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        },
        "risk": {
            "max_loss_per_trade_pct": 0.5,
            "max_trades_per_day": 1,
            "volatile_atr_pct_threshold": 1.3,
            "storm_atr_pct_threshold": 1.4,
            "volatile_size_multiplier": 0.75,
            "storm_size_multiplier": 0.5,
        },
        "execution": {
            "mode": "LIVE",
            "swap_provider": "JUPITER",
            "slippage_bps": 50,
            "min_notional_usdc": 50,
            "only_direct_routes": False,
        },
        "exit": {"stop": "SWING_LOW", "take_profit_r_multiple": 1.5},
        "meta": {"config_version": 2, "note": "test"},
    }


class TradeExecutionApiTest(unittest.TestCase):
    def test_open_position_is_canceled_when_multiplier_is_zero(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            _ = method
            _ = path
            _ = query
            _ = body_json
            return 404, {"error": "not used"}

        with MockServer(responder):
            config = _build_config()
            config["execution"]["min_notional_usdc"] = 20
            persistence = InMemoryPersistence(config)
            lock = InMemoryLock()
            logger = InMemoryLogger()
            sender = FakeSolanaSender()
            execution = JupiterSwapAdapter(JupiterQuoteClient(), sender, logger)

            signal = EntrySignalDecision(
                type="ENTER",
                summary="test enter",
                ema_fast=101,
                ema_slow=100,
                entry_price=100,
                stop_price=98,
                take_profit_price=103,
                diagnostics={
                    "volatility_regime": "STORM",
                    "position_size_multiplier": 0,
                },
            )

            opened = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=logger,
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=config,
                    signal=signal,
                    bar_close_time_iso="2026-02-21T10:00:00.000Z",
                    model_id="ema_pullback_2h_long_v0",
                ),
            )

            self.assertEqual("CANCELED", opened.status)
            self.assertEqual(0, len(sender.sent))
            trade = persistence.trades[opened.trade_id]
            self.assertEqual("CANCELED", trade["state"])
            self.assertEqual("CLOSED", trade["position"]["status"])

    def test_open_and_close_position_hit_jupiter_quote_and_swap_api(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            if method == "GET" and path == "/swap/v1/quote":
                amount = int(query["amount"][0])
                input_mint = query["inputMint"][0]
                output_mint = query["outputMint"][0]
                if input_mint == USDC_MINT and output_mint == SOL_MINT:
                    in_amount = amount
                    out_amount = amount * 12
                elif input_mint == SOL_MINT and output_mint == USDC_MINT:
                    in_amount = amount
                    out_amount = amount // 10
                else:
                    return 400, {"error": "unsupported pair"}
                return 200, {"inAmount": str(in_amount), "outAmount": str(out_amount)}

            if method == "POST" and path == "/swap/v1/swap":
                if not body_json or "quoteResponse" not in body_json:
                    return 400, {"error": "missing quoteResponse"}
                if "userPublicKey" not in body_json:
                    return 400, {"error": "missing userPublicKey"}
                return 200, {"swapTransaction": "AQ=="}

            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            quote_url = f"{server.base_url}/swap/v1/quote"
            swap_url = f"{server.base_url}/swap/v1/swap"
            with patch("pybot.adapters.execution.jupiter_quote_client.QUOTE_API_URL", quote_url), patch(
                "pybot.adapters.execution.jupiter_swap.SWAP_API_URL",
                swap_url,
            ):
                config = _build_config()
                config["execution"]["min_notional_usdc"] = 20
                persistence = InMemoryPersistence(config)
                lock = InMemoryLock()
                logger = InMemoryLogger()
                sender = FakeSolanaSender()
                execution = JupiterSwapAdapter(JupiterQuoteClient(), sender, logger)

                signal = EntrySignalDecision(
                    type="ENTER",
                    summary="test enter",
                    ema_fast=101,
                    ema_slow=100,
                    entry_price=100,
                    stop_price=98,
                    take_profit_price=103,
                )

                opened = open_position(
                    OpenPositionDependencies(
                        execution=execution,
                        lock=lock,
                        logger=logger,
                        persistence=persistence,
                    ),
                    OpenPositionInput(
                        config=config,
                        signal=signal,
                        bar_close_time_iso="2026-02-21T10:00:00.000Z",
                        model_id="ema_pullback_2h_long_v0",
                    ),
                )
                self.assertEqual("OPENED", opened.status)
                self.assertEqual(1, len(sender.sent))

                trade = persistence.trades[opened.trade_id]
                self.assertIn("entry_trigger_price", trade["position"])
                self.assertIn("entry_price", trade["position"])
                self.assertIn("result", trade["execution"])
                self.assertEqual("ESTIMATED", trade["execution"]["result"]["status"])
                close_price = float(trade["position"]["take_profit_price"])
                closed = close_position(
                    ClosePositionDependencies(
                        execution=execution,
                        lock=lock,
                        logger=logger,
                        persistence=persistence,
                    ),
                    ClosePositionInput(
                        config=config,
                        trade=trade,
                        close_reason="TAKE_PROFIT",
                        close_price=close_price,
                    ),
                )
                self.assertEqual("CLOSED", closed.status)
                self.assertEqual("CLOSED", persistence.trades[opened.trade_id]["state"])
                self.assertEqual(2, len(sender.sent))
                closed_trade = persistence.trades[opened.trade_id]
                self.assertIn("exit_trigger_price", closed_trade["position"])
                self.assertIn("exit_price", closed_trade["position"])
                self.assertIn("exit_result", closed_trade["execution"])
                self.assertEqual("ESTIMATED", closed_trade["execution"]["exit_result"]["status"])

                assert server.requests is not None
                quote_calls = [
                    r
                    for r in server.requests
                    if r["method"] == "GET" and r["path"] == "/swap/v1/quote"
                ]
                swap_calls = [
                    r
                    for r in server.requests
                    if r["method"] == "POST" and r["path"] == "/swap/v1/swap"
                ]
                self.assertEqual(2, len(quote_calls), server.requests)
                self.assertEqual(2, len(swap_calls), server.requests)

                self.assertEqual(USDC_MINT, quote_calls[0]["query"]["inputMint"][0])
                self.assertEqual(SOL_MINT, quote_calls[0]["query"]["outputMint"][0])
                self.assertEqual(SOL_MINT, quote_calls[1]["query"]["inputMint"][0])
                self.assertEqual(USDC_MINT, quote_calls[1]["query"]["outputMint"][0])

    def test_short_model_open_and_close_hits_sell_then_buy_paths(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            if method == "GET" and path == "/swap/v1/quote":
                amount = int(query["amount"][0])
                input_mint = query["inputMint"][0]
                output_mint = query["outputMint"][0]
                if input_mint == SOL_MINT and output_mint == USDC_MINT:
                    in_amount = amount
                    out_amount = amount // 10
                elif input_mint == USDC_MINT and output_mint == SOL_MINT:
                    in_amount = amount
                    out_amount = amount * 10
                else:
                    return 400, {"error": "unsupported pair"}
                return 200, {"inAmount": str(in_amount), "outAmount": str(out_amount)}

            if method == "POST" and path == "/swap/v1/swap":
                if not body_json or "quoteResponse" not in body_json:
                    return 400, {"error": "missing quoteResponse"}
                if "userPublicKey" not in body_json:
                    return 400, {"error": "missing userPublicKey"}
                return 200, {"swapTransaction": "AQ=="}

            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            quote_url = f"{server.base_url}/swap/v1/quote"
            swap_url = f"{server.base_url}/swap/v1/swap"
            with patch("pybot.adapters.execution.jupiter_quote_client.QUOTE_API_URL", quote_url), patch(
                "pybot.adapters.execution.jupiter_swap.SWAP_API_URL",
                swap_url,
            ):
                config = _build_config()
                config["direction"] = "SHORT"
                config["strategy"] = {
                    "name": "storm_short_v0",
                    "ema_fast_period": 12,
                    "ema_slow_period": 34,
                    "swing_low_lookback_bars": 12,
                    "entry": "ON_BAR_CLOSE",
                }
                persistence = InMemoryPersistence(config)
                lock = InMemoryLock()
                logger = InMemoryLogger()
                sender = FakeSolanaSender()
                execution = JupiterSwapAdapter(JupiterQuoteClient(), sender, logger)

                signal = EntrySignalDecision(
                    type="ENTER",
                    summary="storm short enter",
                    ema_fast=99,
                    ema_slow=101,
                    entry_price=100,
                    stop_price=102,
                    take_profit_price=97,
                    diagnostics={"volatility_regime": "STORM", "position_size_multiplier": 0.5},
                )

                opened = open_position(
                    OpenPositionDependencies(
                        execution=execution,
                        lock=lock,
                        logger=logger,
                        persistence=persistence,
                    ),
                    OpenPositionInput(
                        config=config,
                        signal=signal,
                        bar_close_time_iso="2026-02-21T12:00:00.000Z",
                        model_id="storm_2h_short_v0",
                    ),
                )
                self.assertEqual("OPENED", opened.status)

                trade = persistence.trades[opened.trade_id]
                self.assertEqual("SHORT", trade["direction"])
                self.assertGreater(float(trade["position"]["quote_amount_usdc"]), 0.0)

                close_price = float(trade["position"]["take_profit_price"])
                closed = close_position(
                    ClosePositionDependencies(
                        execution=execution,
                        lock=lock,
                        logger=logger,
                        persistence=persistence,
                    ),
                    ClosePositionInput(
                        config=config,
                        trade=trade,
                        close_reason="TAKE_PROFIT",
                        close_price=close_price,
                    ),
                )
                self.assertEqual("CLOSED", closed.status)

                assert server.requests is not None
                quote_calls = [
                    r
                    for r in server.requests
                    if r["method"] == "GET" and r["path"] == "/swap/v1/quote"
                ]
                swap_calls = [
                    r
                    for r in server.requests
                    if r["method"] == "POST" and r["path"] == "/swap/v1/swap"
                ]
                self.assertGreaterEqual(len(quote_calls), 3, server.requests)
                self.assertEqual(2, len(swap_calls), server.requests)

    def test_close_position_stop_loss_retries_immediately_until_confirmed(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        trade: TradeRecord = {
            "trade_id": "2026-02-22T20:00:00Z_ema_pullback_2h_long_v0_LONG",
            "model_id": "ema_pullback_2h_long_v0",
            "bar_close_time_iso": "2026-02-22T20:00:00Z",
            "pair": "SOL/USDC",
            "direction": "LONG",
            "state": "CONFIRMED",
            "config_version": 2,
            "execution": {"entry_tx_signature": "entry_sig_1"},
            "position": {
                "status": "OPEN",
                "quantity_sol": 0.5,
                "entry_price": 80.0,
                "stop_price": 78.0,
                "take_profit_price": 84.0,
                "entry_time_iso": "2026-02-22T20:01:00Z",
            },
            "created_at": "2026-02-22T20:01:00Z",
            "updated_at": "2026-02-22T20:01:00Z",
        }
        persistence.create_trade(trade)
        stored_trade = persistence.trades[trade["trade_id"]]

        class RetryExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                if self.submit_calls < 4:
                    raise RuntimeError("temporary rpc error")
                return SwapSubmission(
                    tx_signature=f"exit_sig_{self.submit_calls}",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": f"exit_sig_{self.submit_calls}"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = RetryExecution()
        closed = close_position(
            ClosePositionDependencies(
                execution=execution,
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            ClosePositionInput(
                config=config,
                trade=stored_trade,
                close_reason="STOP_LOSS",
                close_price=77.5,
            ),
        )

        self.assertEqual("CLOSED", closed.status)
        self.assertEqual(4, execution.submit_calls)
        self.assertIn("after 4 attempts", closed.summary)
        closed_trade = persistence.trades[trade["trade_id"]]
        self.assertEqual("CLOSED", closed_trade["state"])
        self.assertEqual("CONFIRMED", closed_trade["execution"]["exit_submission_state"])

    def test_open_position_retries_transient_submit_errors(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="retry enter",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class RetryEntryExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                if self.submit_calls < 3:
                    raise RuntimeError("temporary rpc timeout")
                return SwapSubmission(
                    tx_signature="entry_sig_retry_ok",
                    in_amount_atomic=50_000_000,
                    out_amount_atomic=500_000_000,
                    order={"tx_signature": "entry_sig_retry_ok"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 100.0,
                        "spent_quote_usdc": 50.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = RetryEntryExecution()
        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            opened = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=logger,
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=config,
                    signal=signal,
                    bar_close_time_iso="2026-02-21T10:00:00.000Z",
                    model_id="ema_pullback_2h_long_v0",
                ),
            )

        self.assertEqual("OPENED", opened.status)
        self.assertEqual(3, execution.submit_calls)
        self.assertIn("after 3 attempts", opened.summary)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual("CONFIRMED", trade["state"])

    def test_open_position_does_not_retry_non_retriable_submit_error(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="no retry enter",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class NonRetriableEntryExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                raise RuntimeError("insufficient funds for fee")

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = NonRetriableEntryExecution()
        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            opened = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=logger,
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=config,
                    signal=signal,
                    bar_close_time_iso="2026-02-21T10:00:00.000Z",
                    model_id="ema_pullback_2h_long_v0",
                ),
            )

        self.assertEqual("SKIPPED", opened.status)
        self.assertEqual(1, execution.submit_calls)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual("CANCELED", trade["state"])
        self.assertIn("attempt 1/3", trade["execution"]["entry_error"])
        self.assertEqual("CLOSED", trade["position"]["status"])

    def test_open_position_marks_slippage_error_as_skipped(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="slippage skip enter",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class SlippageExecution:
            def __init__(self) -> None:
                self.submit_calls = 0
                self.slippage_bps_history: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.slippage_bps_history.append(int(request.slippage_bps))
                self.submit_calls += 1
                raise RuntimeError(
                    "RPC sendTransaction failed: {'message': 'Transaction simulation failed: Error processing Instruction 4: custom program error: 0x1771'}"
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = SlippageExecution()
        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            opened = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=logger,
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=config,
                    signal=signal,
                    bar_close_time_iso="2026-02-21T10:00:00.000Z",
                    model_id="ema_pullback_2h_long_v0",
                ),
            )

        self.assertEqual("SKIPPED", opened.status)
        self.assertEqual(3, execution.submit_calls)
        self.assertEqual([50, 50, 51], execution.slippage_bps_history)
        self.assertIn("custom program error: 0x1771", opened.summary)
        self.assertNotIn("'message':", opened.summary)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual("CANCELED", trade["state"])
        self.assertEqual("CLOSED", trade["position"]["status"])
        self.assertIn("attempt 3/3", trade["execution"]["entry_error"])
        self.assertIn("0x1771", trade["execution"]["entry_error"])

    def test_open_position_marks_exact_out_amount_not_matched_as_skipped(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="exact out amount not matched skip enter",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class ExactOutMismatchExecution:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                self.submit_calls += 1
                raise RuntimeError(
                    "RPC sendTransaction failed: {'message': 'Transaction simulation failed: "
                    "Error processing Instruction 4: custom program error: 0x1781'}"
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=False, error="unused")

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = ExactOutMismatchExecution()
        with patch("pybot.app.usecases.open_position.time.sleep", return_value=None):
            opened = open_position(
                OpenPositionDependencies(
                    execution=execution,
                    lock=lock,
                    logger=logger,
                    persistence=persistence,
                ),
                OpenPositionInput(
                    config=config,
                    signal=signal,
                    bar_close_time_iso="2026-02-21T10:00:00.000Z",
                    model_id="ema_pullback_2h_long_v0",
                ),
            )

        self.assertEqual("SKIPPED", opened.status)
        self.assertEqual(3, execution.submit_calls)
        self.assertIn("slippage exceeded", opened.summary)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual("CANCELED", trade["state"])
        self.assertEqual("CLOSED", trade["position"]["status"])
        self.assertIn("attempt 3/3", trade["execution"]["entry_error"])
        self.assertIn("0x1781", trade["execution"]["entry_error"])

    def test_open_position_prefers_balance_snapshot_for_long_position_size(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="balance snapshot long",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class BalanceSnapshotLongExecution:
            def __init__(self) -> None:
                self.quote_balances = [200.0, 200.0, 0.012346]
                self.base_balances = [1.0, 2.999876543]

            @staticmethod
            def _next(values: list[float]) -> float:
                if len(values) > 1:
                    return values.pop(0)
                return values[0]

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                return SwapSubmission(
                    tx_signature="entry_sig_long_balance",
                    in_amount_atomic=200_000_000,
                    out_amount_atomic=2_000_000_000,
                    order={"tx_signature": "entry_sig_long_balance"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 100.0,
                        "spent_quote_usdc": 200.0,
                        "filled_base_sol": 2.0,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return self._next(self.quote_balances)

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return self._next(self.base_balances)

        opened = open_position(
            OpenPositionDependencies(
                execution=BalanceSnapshotLongExecution(),
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=signal,
                bar_close_time_iso="2026-02-21T10:00:00.000Z",
                model_id="ema_pullback_2h_long_v0",
            ),
        )

        self.assertEqual("OPENED", opened.status)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual(199.987654, float(trade["position"]["quote_amount_usdc"]))
        self.assertEqual(1.999876543, float(trade["position"]["quantity_sol"]))

    def test_open_position_prefers_balance_snapshot_for_short_quote_amount(self) -> None:
        config = _build_config()
        config["direction"] = "SHORT"
        config["strategy"] = {
            "name": "storm_short_v0",
            "ema_fast_period": 12,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        }
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="balance snapshot short",
            ema_fast=99.0,
            ema_slow=101.0,
            entry_price=100.0,
            stop_price=102.0,
            take_profit_price=97.0,
        )

        class BalanceSnapshotShortExecution:
            def __init__(self) -> None:
                self.quote_balances = [0.0, 118.371078]
                self.base_balances = [1.2, 1.2, 0.0199]

            @staticmethod
            def _next(values: list[float]) -> float:
                if len(values) > 1:
                    return values.pop(0)
                return values[0]

            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                return SwapSubmission(
                    tx_signature="entry_sig_short_balance",
                    in_amount_atomic=1_180_000_000,
                    out_amount_atomic=118_396_203,
                    order={"tx_signature": "entry_sig_short_balance"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 100.33576525423728,
                        "spent_quote_usdc": 118.396203,
                        "filled_base_sol": 1.18,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return self._next(self.quote_balances)

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return self._next(self.base_balances)

        opened = open_position(
            OpenPositionDependencies(
                execution=BalanceSnapshotShortExecution(),
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=signal,
                bar_close_time_iso="2026-02-21T12:00:00.000Z",
                model_id="storm_2h_short_v0",
            ),
        )

        self.assertEqual("OPENED", opened.status)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual("SHORT", trade["direction"])
        self.assertEqual(118.371078, float(trade["position"]["quote_amount_usdc"]))

    def test_open_position_long_entry_amount_never_exceeds_available_quote(self) -> None:
        config = _build_config()
        config["execution"]["min_notional_usdc"] = 20
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="amount floor long",
            ema_fast=88.1,
            ema_slow=87.9,
            entry_price=88.01,
            stop_price=87.23,
            take_profit_price=89.41,
        )

        class LongAmountFloorExecution:
            def __init__(self) -> None:
                self.submitted_amounts: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submitted_amounts.append(request.amount_atomic)
                return SwapSubmission(
                    tx_signature="entry_sig_long_floor",
                    in_amount_atomic=request.amount_atomic,
                    out_amount_atomic=440_000_000,
                    order={"tx_signature": "entry_sig_long_floor"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 88.01,
                        "spent_quote_usdc": request.amount_atomic / 1_000_000,
                        "filled_base_sol": 0.44,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 88.01

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 38.717328

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        execution = LongAmountFloorExecution()
        opened = open_position(
            OpenPositionDependencies(
                execution=execution,
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=signal,
                bar_close_time_iso="2026-02-26T10:15:00.000Z",
                model_id="ema_pullback_15m_both_v0",
            ),
        )

        self.assertEqual("OPENED", opened.status)
        self.assertEqual([38_330_154], execution.submitted_amounts)
        trade = persistence.trades[opened.trade_id]
        self.assertAlmostEqual(38.330154, float(trade["plan"]["notional_usdc"]), places=6)

    def test_open_position_short_entry_amount_never_exceeds_available_base(self) -> None:
        config = _build_config()
        config["execution"]["min_notional_usdc"] = 20
        config["direction"] = "SHORT"
        config["strategy"] = {
            "name": "storm_short_v0",
            "ema_fast_period": 12,
            "ema_slow_period": 34,
            "swing_low_lookback_bars": 12,
            "entry": "ON_BAR_CLOSE",
        }
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="amount floor short",
            ema_fast=87.9,
            ema_slow=88.3,
            entry_price=88.01,
            stop_price=89.2,
            take_profit_price=86.2,
        )

        class ShortAmountFloorExecution:
            def __init__(self) -> None:
                self.submitted_amounts: list[int] = []

            def submit_swap(self, request: Any) -> SwapSubmission:
                self.submitted_amounts.append(request.amount_atomic)
                return SwapSubmission(
                    tx_signature="entry_sig_short_floor",
                    in_amount_atomic=request.amount_atomic,
                    out_amount_atomic=29_300_000,
                    order={"tx_signature": "entry_sig_short_floor"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 88.01,
                        "spent_quote_usdc": 29.3,
                        "filled_base_sol": request.amount_atomic / 1_000_000_000,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 88.01

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 0.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 0.353333333

        execution = ShortAmountFloorExecution()
        opened = open_position(
            OpenPositionDependencies(
                execution=execution,
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=signal,
                bar_close_time_iso="2026-02-26T10:30:00.000Z",
                model_id="storm_2h_short_v0",
            ),
        )

        self.assertEqual("OPENED", opened.status)
        self.assertEqual([329_999_999], execution.submitted_amounts)
        self.assertLessEqual(execution.submitted_amounts[0], 333_333_333)

    def test_open_position_persists_entry_fee_lamports_when_supported(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        signal = EntrySignalDecision(
            type="ENTER",
            summary="entry fee capture",
            ema_fast=101.0,
            ema_slow=100.0,
            entry_price=100.0,
            stop_price=98.0,
            take_profit_price=103.0,
        )

        class FeeAwareExecution:
            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                return SwapSubmission(
                    tx_signature="entry_sig_fee_capture",
                    in_amount_atomic=100_000_000,
                    out_amount_atomic=1_000_000_000,
                    order={"tx_signature": "entry_sig_fee_capture"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 100.0,
                        "spent_quote_usdc": 100.0,
                        "filled_base_sol": 1.0,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_transaction_fee_lamports(self, tx_signature: str) -> int:
                _ = tx_signature
                return 8_500

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        opened = open_position(
            OpenPositionDependencies(
                execution=FeeAwareExecution(),
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            OpenPositionInput(
                config=config,
                signal=signal,
                bar_close_time_iso="2026-02-21T10:00:00.000Z",
                model_id="ema_pullback_2h_long_v0",
            ),
        )

        self.assertEqual("OPENED", opened.status)
        trade = persistence.trades[opened.trade_id]
        self.assertEqual(8_500, trade["execution"]["entry_fee_lamports"])

    def test_close_position_persists_exit_fee_lamports_when_supported(self) -> None:
        config = _build_config()
        persistence = InMemoryPersistence(config)
        lock = InMemoryLock()
        logger = InMemoryLogger()

        trade: TradeRecord = {
            "trade_id": "2026-02-22T20:00:00Z_ema_pullback_2h_long_v0_LONG",
            "model_id": "ema_pullback_2h_long_v0",
            "bar_close_time_iso": "2026-02-22T20:00:00Z",
            "pair": "SOL/USDC",
            "direction": "LONG",
            "state": "CONFIRMED",
            "config_version": 2,
            "execution": {"entry_tx_signature": "entry_sig_1"},
            "position": {
                "status": "OPEN",
                "quantity_sol": 0.5,
                "entry_price": 80.0,
                "stop_price": 78.0,
                "take_profit_price": 84.0,
                "entry_time_iso": "2026-02-22T20:01:00Z",
            },
            "created_at": "2026-02-22T20:01:00Z",
            "updated_at": "2026-02-22T20:01:00Z",
        }
        persistence.create_trade(trade)
        stored_trade = persistence.trades[trade["trade_id"]]

        class FeeAwareExecution:
            def submit_swap(self, request: Any) -> SwapSubmission:
                _ = request
                return SwapSubmission(
                    tx_signature="exit_sig_fee_capture",
                    in_amount_atomic=500_000_000,
                    out_amount_atomic=40_000_000,
                    order={"tx_signature": "exit_sig_fee_capture"},
                    result={
                        "status": "ESTIMATED",
                        "avg_fill_price": 80.0,
                        "spent_quote_usdc": 40.0,
                        "filled_base_sol": 0.5,
                    },
                )

            def confirm_swap(self, tx_signature: str, timeout_ms: int) -> SwapConfirmation:
                _ = tx_signature
                _ = timeout_ms
                return SwapConfirmation(confirmed=True)

            def get_transaction_fee_lamports(self, tx_signature: str) -> int:
                _ = tx_signature
                return 12_000

            def get_mark_price(self, pair: str) -> float:
                _ = pair
                return 77.5

            def get_available_quote_usdc(self, pair: str) -> float:
                _ = pair
                return 100.0

            def get_available_base_sol(self, pair: str) -> float:
                _ = pair
                return 1.0

        closed = close_position(
            ClosePositionDependencies(
                execution=FeeAwareExecution(),
                lock=lock,
                logger=logger,
                persistence=persistence,
            ),
            ClosePositionInput(
                config=config,
                trade=stored_trade,
                close_reason="STOP_LOSS",
                close_price=77.5,
            ),
        )

        self.assertEqual("CLOSED", closed.status)
        closed_trade = persistence.trades[trade["trade_id"]]
        self.assertEqual(12_000, closed_trade["execution"]["exit_fee_lamports"])

    def test_submit_swap_retries_quote_and_swap_http_503(self) -> None:
        quote_count = 0
        swap_count = 0

        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            nonlocal quote_count, swap_count
            _ = query
            _ = body_json
            if method == "GET" and path == "/swap/v1/quote":
                quote_count += 1
                if quote_count == 1:
                    return 503, {"error": "temporary unavailable"}
                return 200, {"inAmount": "50000000", "outAmount": "500000000"}
            if method == "POST" and path == "/swap/v1/swap":
                swap_count += 1
                if swap_count == 1:
                    return 503, {"error": "temporary unavailable"}
                return 200, {"swapTransaction": "AQ=="}
            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            quote_url = f"{server.base_url}/swap/v1/quote"
            swap_url = f"{server.base_url}/swap/v1/swap"
            sender = FakeSolanaSender()
            adapter = JupiterSwapAdapter(JupiterQuoteClient(), sender, InMemoryLogger())
            with patch("pybot.adapters.execution.jupiter_quote_client.QUOTE_API_URL", quote_url), patch(
                "pybot.adapters.execution.jupiter_swap.SWAP_API_URL",
                swap_url,
            ), patch(
                "pybot.adapters.execution.http_retry.time.sleep",
                return_value=None,
            ):
                submission = adapter.submit_swap(
                    SubmitSwapRequest(
                        side="BUY_SOL_WITH_USDC",
                        amount_atomic=50_000_000,
                        slippage_bps=12,
                        only_direct_routes=False,
                    )
                )

        self.assertEqual("sig-1", submission.tx_signature)
        self.assertEqual(2, quote_count)
        self.assertEqual(2, swap_count)
        self.assertEqual(1, len(sender.sent))


class SolanaSenderRpcMethodTest(unittest.TestCase):
    def test_rpc_retries_on_http_503_then_succeeds(self) -> None:
        request_count = 0

        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            nonlocal request_count
            _ = query
            if method == "POST" and path == "/rpc" and body_json and body_json.get("method") == "getBalance":
                request_count += 1
                if request_count == 1:
                    return 503, {"error": "temporary unavailable"}
                return 200, {"jsonrpc": "2.0", "id": 1, "result": {"value": 2_000_000_000}}
            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            fake_secret = bytes(Keypair())
            with patch(
                "pybot.adapters.execution.solana_sender._decrypt_secret_key",
                return_value=fake_secret,
            ), patch("pybot.adapters.execution.solana_sender.time.sleep", return_value=None):
                sender = SolanaSender(
                    rpc_url=f"{server.base_url}/rpc",
                    wallet_key_path="unused",
                    wallet_passphrase="unused",
                    logger=InMemoryLogger(),
                )
                balance = sender.get_native_sol_balance_ui_amount()

        self.assertEqual(2.0, balance)
        self.assertEqual(2, request_count)

    def test_send_versioned_transaction_uses_send_transaction_rpc_method(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            _ = query
            if (
                method == "POST"
                and path == "/rpc"
                and body_json
                and body_json.get("method") == "sendTransaction"
            ):
                return 200, {"jsonrpc": "2.0", "id": 1, "result": "rpc_sig_123"}
            return 200, {"jsonrpc": "2.0", "id": 1, "result": None}

        class DummyVersionedTransaction:
            def __init__(self, message: str):
                self.message = message

            @staticmethod
            def from_bytes(payload: bytes) -> "DummyVersionedTransaction":
                _ = payload
                return DummyVersionedTransaction(message="dummy_message")

            @staticmethod
            def populate(message: str, signatures: list[Any]) -> Any:
                _ = message
                _ = signatures

                class SignedTx:
                    def __bytes__(self) -> bytes:
                        return b"\x00\x01"

                return SignedTx()

        with MockServer(responder) as server:
            fake_secret = bytes(Keypair())
            with patch(
                "pybot.adapters.execution.solana_sender._decrypt_secret_key",
                return_value=fake_secret,
            ), patch(
                "pybot.adapters.execution.solana_sender.VersionedTransaction",
                DummyVersionedTransaction,
            ), patch(
                "pybot.adapters.execution.solana_sender.to_bytes_versioned",
                return_value=b"message",
            ):
                sender = SolanaSender(
                    rpc_url=f"{server.base_url}/rpc",
                    wallet_key_path="unused",
                    wallet_passphrase="unused",
                    logger=InMemoryLogger(),
                )
                signature = sender.send_versioned_transaction_base64("AQ==")
                self.assertEqual("rpc_sig_123", signature)

            assert server.requests is not None
            rpc_methods = [r["body_json"]["method"] for r in server.requests if r.get("body_json")]
            self.assertIn("sendTransaction", rpc_methods)
            self.assertNotIn("sendRawTransaction", rpc_methods)

    def test_get_transaction_fee_lamports_reads_meta_fee(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            _ = query
            if (
                method == "POST"
                and path == "/rpc"
                and body_json
                and body_json.get("method") == "getTransaction"
            ):
                return 200, {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "meta": {"fee": 9_400},
                    },
                }
            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            fake_secret = bytes(Keypair())
            with patch(
                "pybot.adapters.execution.solana_sender._decrypt_secret_key",
                return_value=fake_secret,
            ):
                sender = SolanaSender(
                    rpc_url=f"{server.base_url}/rpc",
                    wallet_key_path="unused",
                    wallet_passphrase="unused",
                    logger=InMemoryLogger(),
                )
                fee = sender.get_transaction_fee_lamports("sig-1")

        self.assertEqual(9_400, fee)

    def test_get_transaction_fee_lamports_returns_none_when_missing(self) -> None:
        def responder(
            method: str,
            path: str,
            query: dict[str, list[str]],
            body_json: dict[str, Any] | None,
        ) -> tuple[int, dict[str, Any]]:
            _ = query
            if (
                method == "POST"
                and path == "/rpc"
                and body_json
                and body_json.get("method") == "getTransaction"
            ):
                return 200, {"jsonrpc": "2.0", "id": 1, "result": {"meta": {}}}
            return 404, {"error": "not found"}

        with MockServer(responder) as server:
            fake_secret = bytes(Keypair())
            with patch(
                "pybot.adapters.execution.solana_sender._decrypt_secret_key",
                return_value=fake_secret,
            ):
                sender = SolanaSender(
                    rpc_url=f"{server.base_url}/rpc",
                    wallet_key_path="unused",
                    wallet_passphrase="unused",
                    logger=InMemoryLogger(),
                )
                fee = sender.get_transaction_fee_lamports("sig-1")

        self.assertIsNone(fee)


if __name__ == "__main__":
    unittest.main()
