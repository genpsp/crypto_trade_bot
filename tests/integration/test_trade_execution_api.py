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

    def set_inflight_tx(self, signature: str, ttl_seconds: int) -> None:
        self.inflight[signature] = ttl_seconds

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
        "direction": "LONG_ONLY",
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
                    ),
                )
                self.assertEqual("OPENED", opened.status)
                self.assertEqual(1, len(sender.sent))

                trade = persistence.trades[opened.trade_id]
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


class SolanaSenderRpcMethodTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
