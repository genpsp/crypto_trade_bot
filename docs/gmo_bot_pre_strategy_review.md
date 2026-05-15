# gmo_bot プレ・ストラテジーレビュー（2026-05-15）

対象: `apps/gmo_bot/` 配下のうち、売買シグナル / 板取りロジック「以前」の構造・実装・運用上の論点。

レビュー範囲: `main.py`, `reports.py`, `app/usecases/*`, `app/ports/*`, `adapters/execution/*`, `adapters/market_data/*`, `adapters/persistence/*`, `adapters/lock/*`, `infra/*`, `domain/model/*`, `domain/strategy/{registry,risk_constants}.py`, `domain/utils/time.py`。
**対象外**: `domain/strategy/models/ema_trend_pullback_15m_v0.py` のシグナル判定そのもの。

凡例: **重大度** High（運用事故 / データ不整合に直結）/ Mid（保守性・コスト・将来バグ温床）/ Low（清書・命名）。

---

## 1. バグ・潜在的バグ

### 1.1 [High] `_clear_open_trade_state` で例外を完全に黙殺
[apps/gmo_bot/adapters/persistence/firestore_repo.py:292-296](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L292-L296)

```python
def _clear_open_trade_state(self) -> None:
    try:
        self._set_no_open_trade_state()
    except Exception:
        pass
```

state ドキュメントの「オープン無し」マーキングに失敗してもログ無し。以後 `find_open_trade` が古い `OPEN_TRADE_STATE` を信じ続け、二重エントリの引き金になり得る。**少なくとも `logger.error` を出し、上位に再 raise するか、SLO アラートに乗せる**。

### 1.2 [High] Firestore Watch の unsubscribe で例外を完全に黙殺
[apps/gmo_bot/infra/bootstrap.py:158-166](../apps/gmo_bot/infra/bootstrap.py#L158-L166)

`_unsubscribe_watch` が `except Exception: pass`。リスナの掃除漏れ・スレッド残留がサイレントに発生する。warn ログでよいので出すべき。

### 1.3 [High] 補助スレッドの本体に try/except が無く、例外で機能停止
[apps/gmo_bot/infra/bootstrap.py:488-509, 511-519, 574-591](../apps/gmo_bot/infra/bootstrap.py#L488-L591)

`_watchdog_loop`, `_exit_fallback_loop`, `_open_trade_poll_loop` の `while` 本体に try/except 無し。`notifier.notify_*` や `_run_model_cycle` の中で 1 度でも例外が逃げると daemon スレッドが死に、`main` は生き続ける → ステイル監視 / フォールバック cycle / 高頻度ポーリングがサイレント停止する。`cron_cycle._runner` の `try/except` と同等の保護を全 loop に入れる（[apps/gmo_bot/infra/scheduler/cron_cycle.py:34-37](../apps/gmo_bot/infra/scheduler/cron_cycle.py#L34-L37)）。

### 1.4 [High] `close_position` の部分約定リトライループに上限が無い
[apps/gmo_bot/app/usecases/close_position.py:744-789](../apps/gmo_bot/app/usecases/close_position.py#L744-L789)

`while remaining_lots:` で逐次クローズ。GMO 側が `submit_close_order` ごとに極小サイズしか食ってくれないケース（保守直前 / 一時的に板枯れ）で、API コール量が爆発し runner_lock TTL（600s）も食い潰す。**最大反復回数（例: 5）と進捗チェック（前回より `remaining_size` が `size_step` 以上減ったか）を入れる**。

### 1.5 [High] `requests.Session` をマルチスレッドで共有
[apps/gmo_bot/adapters/execution/gmo_api_client.py:31](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L31)

`GmoApiClient` は cron / watchdog / exit_fallback / open_trade_poll / ws_monitor から並行に呼ばれるが、`requests.Session` は thread-safe を保証していない。コネクションプール競合や header 漏えいリスク。**thread-local Session か `urllib3.PoolManager` ベースに切り替えるか、ロックで保護する**。

### 1.6 [High] HTTP リトライ無し
[apps/gmo_bot/adapters/execution/gmo_api_client.py:244-288](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L244-L288)

`_request` は 1 発勝負。502/503/timeout で即 raise。rate-limit リトライは `get_mark_price` のみ実装（[gmo_margin_execution.py:160-181](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L160-L181)）で、他の private 系（注文取得 / 約定取得 / cancel）には無い。
特に `confirm_order` のループ内（[gmo_margin_execution.py:128-151](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L128-L151)）で transient エラーが起きると確認できず FAILED 化する。**最低限 GET 系 + cancel_order に exponential backoff + jitter のリトライを入れる**。

### 1.7 [High] `recent_closed_state` の更新がトランザクションでない
[apps/gmo_bot/adapters/persistence/firestore_repo.py:419-447](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L419-L447)

`_append_recent_closed_trade` は「キャッシュ読み → merge → set(merge=True)」だが、複数プロセス / リスナ駆動の更新が並走するとマージ漏れで直近 closed トレードを取りこぼす。**Firestore Transaction（`firestore.transaction()` + `get`+`set`）で書く**。または「items を append でなく `arrayUnion` + 後段で trim」にする。

### 1.8 [High] `_resolve_trade_update_date` のフォールバックで日付が今日に化ける
[apps/gmo_bot/adapters/persistence/firestore_repo.py:204-215](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L204-L215)

cache 未ヒット & payload に `trade_date` 無し & trade_id 先頭が日付として parse 失敗 → JST 今日の日付に書く。古いトレードの更新が今日の day doc 配下に分裂し、`get_trade` のロードが失敗し得る。**フォールバック時は warn を出し、可能なら例外にする**。

### 1.9 [High] `_extract_trade_date_from_payload` の優先順位が暗黙
[apps/gmo_bot/adapters/persistence/firestore_repo.py:75-87](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L75-L87)

`trade_date` → `created_at` → `bar_close_time_iso` → trade_id 先頭 → 今日 の順で fallthrough。`updated_at` は無視。`updated_at` のみがあるトレードの inplace migration 時にズレる。**`updated_at` をフォールバックに含めるか、`trade_date` が必須であることをスキーマで強制する**。

### 1.10 [High] `paper_execution._submitted_results` がアンバウンド
[apps/gmo_bot/adapters/execution/paper_execution.py:26](../apps/gmo_bot/adapters/execution/paper_execution.py#L26)

ペーパー実行で order を出すたび `dict` に蓄積され続け、長期稼働でメモリリーク。LIVE と異なり PAPER は常に有効化される可能性があるので **LRU か confirm 後の pop が必要**。

### 1.11 [Mid] `acquire_runner_lock` が既存トークンを上書き
[apps/gmo_bot/adapters/lock/redis_lock.py:36-42](../apps/gmo_bot/adapters/lock/redis_lock.py#L36-L42)

同一 `RedisLockAdapter` インスタンスで二度 `acquire_runner_lock` を呼ぶと、旧トークンが捨てられて Redis 上のロックを「自分のロックではない」と判定して release に失敗する。現状コード上は二重 acquire は起こらないはずだが、防御的に `self.runner_lock_token is not None` のとき `False` を返すか assert すべき。

### 1.12 [Mid] `acquire_runner_lock` の release が release_script の戻り値の型に依存
[apps/gmo_bot/adapters/lock/redis_lock.py:50-57](../apps/gmo_bot/adapters/lock/redis_lock.py#L50-L57)

`released != 1` で warn を出すが、Redis Lua `redis.call("del", ...)` は int を返すので redis-py 5.x なら問題なし。ただし `decode_responses=True` で `str` 化されるパスは無いか、`redis-py` のバージョン依存で挙動が変わらないかを軽くテストすべき（明示的に `int(released) == 1` に統一すると安全）。

### 1.13 [Mid] `gmo_margin_execution` がペア固定
[apps/gmo_bot/adapters/execution/gmo_margin_execution.py:44, 57, 73, 154, 200, 220, 227](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L44)

`symbol = PAIR_SYMBOL_MAP["SOL/JPY"]` のリテラル固定。引数の `pair` を受け取っているメソッド（`get_mark_price`, `get_symbol_rule`）でも実際は無視。将来のペア追加で必ず踏むバグの種。**`PAIR_SYMBOL_MAP[pair]` を一貫して使う**。

### 1.14 [Mid] `gmo_margin_execution.submit_entry_order` がペアを受け取らない
[apps/gmo_bot/adapters/execution/gmo_margin_execution.py:43-54](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L43-L54)

`SubmitEntryOrderRequest` にも `pair` フィールド無し。多通貨対応への移行が困難。**ポート定義に `pair` を追加する**。

### 1.15 [Mid] `confirm_order` 内の `time.sleep(POLL_INTERVAL_SECONDS)` の前に状態判定の途中で raise
[apps/gmo_bot/adapters/execution/gmo_margin_execution.py:128-151](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L128-L151)

`get_executions` / `get_order` の transient エラーで break せず raise すれば、ループは中断され、約定済み注文を見失う。`try/except Exception:` でログ + `continue` の方が安全（ただし最終的に `confirm = False` を返す）。

### 1.16 [Mid] `_round_down_to_step` の float 演算で誤差
[apps/gmo_bot/adapters/execution/gmo_margin_execution.py:370-374](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L370-L374)

`step = 0.01` 等の場合 `math.floor(value / step) * step` で `0.029999999...` のような結果が起こり得る。`round(*, 10)` で誤魔化しているが、`Decimal` ベースの `_round_price_to_step`（[同上 383-389行](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L383-L389)）と統一すべき。

### 1.17 [Mid] `get_mark_price` の retry が固定 (0.2, 0.4) で jitter 無し
[apps/gmo_bot/adapters/execution/gmo_margin_execution.py:27, 160-181](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L160)

レート制限時に複数モデルが同時に同じ delay でリトライする「thundering herd」を誘発しがち。jitter 化を推奨。

### 1.18 [Mid] `bootstrap.config_fingerprint` の不安定性
[apps/gmo_bot/infra/bootstrap.py:413](../apps/gmo_bot/infra/bootstrap.py#L413)

`hashlib.sha1(repr(config).encode("utf-8")).hexdigest()[:12]` は Firestore から取った dict のキー順、ネストされた配列の repr、float の repr に依存。Firestore SDK が同じ payload を別順序で返した瞬間に fingerprint が変わり、`_refresh_runtime_specs` で repo が無駄に再生成される。**`json.dumps(config, sort_keys=True, default=str)` に置換**。

### 1.19 [Mid] `signal.signal` 後の例外ハンドラで logger 二重生成
[apps/gmo_bot/main.py:33-38](../apps/gmo_bot/main.py#L33-L38)

`main()` 内で raise された場合に `create_logger("gmo-bot")` を再度呼んでログ出力する。`logger` を try の外で先に作るか、`main()` 内の例外を `main()` 内で扱う方が筋。

### 1.20 [Mid] `_open_trade_poll_loop` と cron の `_run_all_models` が同じ trade を並行で touch し得る
[apps/gmo_bot/infra/bootstrap.py:566-591](../apps/gmo_bot/infra/bootstrap.py#L566-L591)

Redis runner_lock があるため最終的に直列化されるが、ロック失敗時の SKIPPED が高頻度で Firestore に書き込まれる。`_should_persist_run_record` は SKIPPED の一部を弾くので大量にはならないが、`SKIPPED: lock:runner already acquired by another process` の書き込みは `_should_persist_run_record` を抜けてしまう経路がないか確認（`save_run` に `run["result"] = "SKIPPED"` で渡る一方、`summary` に `lock:runner` を含むので `_is_execution_error_skip_summary` / `_is_market_data_maintenance_skip_summary` のどちらにもマッチせず、`_should_persist_run_record` で `False` → 保存されない、はずなので OK。コメントを追加するか、明示的に early-return することで意図を残す）。

### 1.21 [Mid] `reconcile_protective_exit_execution` の `UNAVAILABLE` を呼び出し側が silent に通過
[apps/gmo_bot/app/usecases/close_position.py:487-491, 719-742](../apps/gmo_bot/app/usecases/close_position.py#L487-L742)

`reconciled_stop.status == "UNAVAILABLE"` の場合、`close_position` は何もせず手動クローズに突入する。「期待していた stop_loss order がそもそも見つからなかった」状態の警告が無い。`logger.warn` を 1 行追加するべき。

### 1.22 [Mid] `arm_protective_exit_orders` の `take_profit_order_status = "CLIENT_MANAGED"` 上書き
[apps/gmo_bot/app/usecases/protective_exit_orders.py:289](../apps/gmo_bot/app/usecases/protective_exit_orders.py#L289)

以前の TP 注文 ID が残っていても `take_profit_order_id` を pop してから `CLIENT_MANAGED` に強制上書き。誤って `EXECUTED` の TP を `CLIENT_MANAGED` に塗り替える経路がないか確認すべき（呼び出し前に `has_active_protective_exit_orders` で防げているはずだが、防御を関数内に持たせるとより安全）。

### 1.23 [Mid] `_handle_order_event` の EXPIRED ハンドリングで `find_open_trade` が None のとき silent return
[apps/gmo_bot/infra/execution/exit_order_monitor.py:133-136](../apps/gmo_bot/infra/execution/exit_order_monitor.py#L133-L136)

stop 注文が EXPIRED した後に open_trade が消えていたら何も警告せず終わる。実運用で「stop 注文だけ expired したが既に閉じている」というレース時にはそれで良いが、warn ログを残すべき。

### 1.24 [Mid] `open_position` で `position_size_multiplier > 1.0` を `RuntimeError` で落とす
[apps/gmo_bot/app/usecases/open_position.py:103-104](../apps/gmo_bot/app/usecases/open_position.py#L103-L104)

dex_bot 側のストラテジー診断値が GMO 制約と合わない場合に、サイクル全体を FAILED に落とす。連続失敗カウンタの誤発火を招くので、**`HOLD` / `SKIPPED_ENTRY` で抜ける扱いが望ましい**。

### 1.25 [Mid] `open_position` の post-fill stop 距離調整で再 stop_distance チェック漏れ
[apps/gmo_bot/app/usecases/open_position.py:265-273, 302-310](../apps/gmo_bot/app/usecases/open_position.py#L265-L310)

`final_stop = pct_stop` に置き換えたあと、もう一度 `stop_distance_pct` を計算していない。`pct_stop` の距離が `MIN_STOP_DISTANCE_PCT` を満たすことは保証しているはずだが、ロジックが暗黙的すぎる。assert を入れて将来の改修事故を防ぐ。

### 1.26 [Mid] `_send` の dedupe で in-memory `_last_sent_by_key` がアンバウンド
[apps/gmo_bot/infra/alerting/slack_notifier.py:77, 364-372](../apps/gmo_bot/infra/alerting/slack_notifier.py#L77)

`trade_closed:{trade_id}:{reason}` のようなユニーク鍵がプロセスライフ中に無限増加。Redis dedupe が有効でも `_last_sent_by_key[dedupe_key] = now` で in-memory 側にも書く（364行）。長期稼働でじわじわ膨れる。**Redis dedupe があるならローカル辞書は touch しない、もしくは max size を設ける**。

---

## 2. 並行性・冪等性

### 2.1 [High] Firestore リスナのコールバックが他スレッドから呼ばれる
[apps/gmo_bot/infra/bootstrap.py:174-187](../apps/gmo_bot/infra/bootstrap.py#L174-L187)

`on_snapshot` のコールバックは Firestore SDK の内部スレッドから呼ばれる。中で `runtime_refresh_needed.set()` だけなので OK だが、`logger.info` も呼んでおり logger.print が他スレッドから来る点だけ意識。

### 2.2 [Mid] `_model_config_listeners` の `model_id` クロージャ束縛
[apps/gmo_bot/infra/bootstrap.py:195-199](../apps/gmo_bot/infra/bootstrap.py#L195-L199)

`lambda ..., mid=added_id: ...` でデフォルト引数キャプチャを使っているので OK。ただ可読性のため `functools.partial` の方が意図が伝わる。

### 2.3 [Mid] `_active_model_contexts()` がスナップショットを取らない
[apps/gmo_bot/infra/bootstrap.py:222-223](../apps/gmo_bot/infra/bootstrap.py#L222-L223)

イテレート中に `_refresh_runtime_specs` から `model_contexts.pop(...)` される可能性。`dict.copy()` 経由でスナップショットを取るほうが安全。

### 2.4 [Mid] `_open_trade_cache` を deepcopy せずに mutate する経路
[apps/gmo_bot/adapters/persistence/firestore_repo.py:298-314](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L298-L314)

`_set_open_trade_cache` は deepcopy するが、`_merge_open_trade_cache` 内で `deepcopy(cached)` → `_deep_merge_dict` → `_set_open_trade_cache(merged)` の経路で **新しい cache に上書き**するため OK。これは想定通りだが、`find_open_trade` で返す前にも deepcopy しており、外部ミュータブル変更からは守られている。問題なし（記録）。

---

## 3. 時刻・タイムゾーン

### 3.1 [Mid] `JST = timezone(timedelta(hours=9))` の 5 重定義
- [firestore_repo.py:48](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L48)
- [ohlcv_provider.py:22](../apps/gmo_bot/adapters/market_data/ohlcv_provider.py#L22)
- [reports.py:29](../apps/gmo_bot/reports.py#L29)
- [daily_trade_summary.py:7](../apps/gmo_bot/infra/alerting/daily_trade_summary.py#L7)
- [domain/utils/time.py:12](../apps/gmo_bot/domain/utils/time.py#L12)

`domain/utils/time.py` の `JST` を単一の真実源として、他は import するように統一する。

### 3.2 [Mid] `bar_close_time_iso` の `Z` 置換が散在
`isoformat().replace("+00:00", "Z")` が `time.py`, `firestore_repo.py`, `run_cycle.py` などで多重実装。ユーティリティ化（例: `format_iso_utc(dt)`）すべき。

### 3.3 [Low] `_runner` の cron 計算が「次の minute 境界」をすべて同期させる
[apps/gmo_bot/infra/scheduler/cron_cycle.py:18-21](../apps/gmo_bot/infra/scheduler/cron_cycle.py#L18-L21)

全モデルが UTC 00 秒ジャストで一斉に動く。GMO API への瞬間負荷が集中する。jitter（±1〜3秒）を入れるとより親切。

---

## 4. 金額・数量

### 4.1 [High] 金額・数量計算が全部 `float`
[apps/gmo_bot/app/usecases/close_position.py:180-225](../apps/gmo_bot/app/usecases/close_position.py#L180-L225) 他多数

`filled_quote_jpy = price * size` の集計、`realized_pnl_jpy` の差分、`exit_fee_jpy + fee_jpy` の加算、すべて float。JPY は整数円が基本なので Decimal で扱うべき。少なくとも **「比較・等価判定では epsilon を必ず使う」「永続化前に明示的に `round()` する」を全域で徹底する**。

### 4.2 [Mid] `POSITION_SIZE_EPSILON = 1e-9` の 4 重定義
- [close_position.py:26](../apps/gmo_bot/app/usecases/close_position.py#L26)
- [gmo_margin_execution.py:29](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L29)
- [protective_exit_orders.py:17](../apps/gmo_bot/app/usecases/protective_exit_orders.py#L17)
- [daily_trade_summary.py:8](../apps/gmo_bot/infra/alerting/daily_trade_summary.py#L8)

共通モジュールに集約。

### 4.3 [Mid] `_decimal_str` の重複
- [gmo_api_client.py:291-293](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L291-L293)
- [gmo_margin_execution.py:392-394](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L392-L394)

同一実装が二箇所。集約。

---

## 5. 設定・スキーマ

### 5.1 [Mid] `infra/config/schema.py` が `apps.dex_bot.infra.config.schema` の private 関数を import
[apps/gmo_bot/infra/config/schema.py:5](../apps/gmo_bot/infra/config/schema.py#L5)

```python
from apps.dex_bot.infra.config.schema import _parse_exit, _parse_risk, _parse_strategy, _require
```

`_` プレフィックスは private を表す慣習を破っている。`shared/config/schema_primitives.py` のような公開モジュールに昇格させる。

### 5.2 [Mid] `gmo_bot` が `apps.dex_bot.*` に直接依存
import を grep すると以下が `apps.dex_bot.*` 由来:
- `apps.dex_bot.domain.risk.loss_streak_trade_cap`
- `apps.dex_bot.domain.risk.short_regime_guard`
- `apps.dex_bot.domain.risk.short_stop_loss_cooldown`
- `apps.dex_bot.domain.risk.swing_low_stop`
- `apps.dex_bot.domain.model.types`
- `apps.dex_bot.domain.strategy.models.*`
- `apps.dex_bot.infra.config.schema`

「アプリ → アプリ」の双方向依存は monorepo 設計の典型的アンチパターン。dex_bot 側の改修で gmo_bot が壊れる。**共通コードは `shared/` または `core/` 配下に移し、双方ともそこを参照する形にする**。

### 5.3 [Mid] `_build_strategy_execution_bridge` で `min_notional_usdc` キーに JPY 値を入れる
[apps/gmo_bot/app/usecases/run_cycle.py:138-142](../apps/gmo_bot/app/usecases/run_cycle.py#L138-L142)

```python
return {"min_notional_usdc": max(float(runtime_config["execution"]["min_notional_jpy"]), 1.0)}
```

ストラテジー実装（dex_bot 由来）が `min_notional_usdc` を見るために命名を流用しているが、現場のレビュアは必ずバグだと思って二度見する。ストラテジー側のキー名を汎用化するか、bridge 層でリネームコメントを 1 行残す。

### 5.4 [Mid] `schema.parse_config` のビジネスバリデーション粒度
[apps/gmo_bot/infra/config/schema.py:65-81](../apps/gmo_bot/infra/config/schema.py#L65-L81)

`slippage_bps > 0` / `leverage_multiplier <= 2.0` / `margin_usage_ratio <= 1.0` 程度のチェック。`take_profit_r_multiple` の上限、`max_loss_per_trade_pct` の上下限、`min_notional_jpy` の妥当範囲（取引所最低額以上）は確認していない。

### 5.5 [Mid] `load_env` がシークレットの最小長を validate しない
[apps/gmo_bot/infra/config/env.py:32-48](../apps/gmo_bot/infra/config/env.py#L32-L48)

空文字 / None だけチェック。`GMO_API_KEY` に "TODO" など typo が入っていても通過。最低限「英数字 + 一定長」を確認する。

### 5.6 [Low] `is_global_pause_enabled` が `is True` 厳密判定
[apps/gmo_bot/infra/config/firestore_config_repo.py:56](../apps/gmo_bot/infra/config/firestore_config_repo.py#L56)

Firestore コンソールから手動で `"true"` (文字列) を入れた場合に効かない。誤入力を `logger.warn` で通知すると運用ミス防止になる。

---

## 6. Firestore コスト・データモデル

### 6.1 [Mid] `_touch_model_metadata` が毎回の create/update_trade で書き込み
[apps/gmo_bot/adapters/persistence/firestore_repo.py:169-179, 519, 533, 647](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L169-L179)

`trade` の更新、daily_balance の保存ごとに `models/{model_id}` の `updated_at_iso` を書き換える。月数百万 writes になり得る。**ハートビート用途なら 1 日 1 回に絞る**。

### 6.2 [Mid] `list_recent_daily_balances` が全件取得後にスライス
[apps/gmo_bot/adapters/persistence/firestore_repo.py:662-680](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L662-L680)

`stream()` で全件読み込んでから Python で sort + `[-days:]`。365 日経った時点で 365 reads。**`order_by("snapshot_date_jst", direction=DESCENDING).limit(days)` を使う**。`list_daily_balances_in_range` も同様（`stream()` 後に範囲フィルタ）。

### 6.3 [Mid] `_scan_open_trade` が日付ドキュメントをフルストリーム
[apps/gmo_bot/adapters/persistence/firestore_repo.py:357-388](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L357-L388)

`self._trades_collection().stream()` で全 day doc を列挙、その後各日付について subcollection を `where` フィルタで取得。day doc 数が増えるほど read コスト増大。state ドキュメント（`_open_trade_state_doc`）優先で当たるので通常パスでは呼ばれないが、フォールバック時のコストが想定外に高い。**期間（直近 N 日）に絞る**か、**state ドキュメントの不整合を頻度低めに監視して自動修復するジョブを分離**。

### 6.4 [Mid] `_scan_recent_closed_trades` の short-circuit ロジック
[apps/gmo_bot/adapters/persistence/firestore_repo.py:480-482](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L480-L482)

`if len(trades_by_id) >= limit * 3: break` だが、各日付で取得する `trade` はすでに `where("state","==","CLOSED")` で絞られているので、`* 3` の意図が読めない。コメントで意図を補足するか、`limit` ぴったりで break する。

### 6.5 [Low] `_extract_run_date` のフォールバックが「JST 今日」
[apps/gmo_bot/adapters/persistence/firestore_repo.py:51-59](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L51-L59)

`bar_close_time_iso` も `executed_at_iso` も無い RunRecord が来た場合に今日の day doc に書き込む。`_should_persist_run_record` で OPENED/CLOSED/PARTIALLY_CLOSED/FAILED/特定 SKIPPED のみ通すので実害は小さいが、warn を出すべき。

### 6.6 [Low] `_sort_trade_key` で `created_at` 比較が文字列辞書順
[apps/gmo_bot/adapters/persistence/firestore_repo.py:110-122](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L110-L122)

ISO8601 + UTC（"Z"）なら文字列辞書順 == 時刻順なので OK。timezone offset 表記が混在し始めると壊れる。

---

## 7. WebSocket / 監視

### 7.1 [Mid] WS が長期間接続失敗してもアラートしない
[apps/gmo_bot/adapters/execution/private_ws_client.py:70-93](../apps/gmo_bot/adapters/execution/private_ws_client.py#L70-L93)

`run_forever` が落ちる → `RECONNECT_DELAY_SECONDS` (3 秒) で再試行を無限ループ。サブスクリプションが永久に失敗していてもログ warn のみ。**N 分以上 subscribed 状態に達しなければ stale_cycle と同様の Slack アラートを出す**べき（フォールバックポーリングは生きるが、TP/SL のリアルタイム性が落ちる）。

### 7.2 [Mid] `_on_message` の `payload.get("error") is not None` でも処理続行のロジックが薄い
[apps/gmo_bot/adapters/execution/private_ws_client.py:121-123](../apps/gmo_bot/adapters/execution/private_ws_client.py#L121-L123)

エラー payload を warn して return するが、subscribe 失敗を意味する error の場合は再 subscribe か WS 再接続をトリガするべき。

### 7.3 [Mid] `extend_ws_access_token` 失敗時に再生成しない
[apps/gmo_bot/adapters/execution/private_ws_client.py:95-104](../apps/gmo_bot/adapters/execution/private_ws_client.py#L95-L104)

token 延長 API が失敗するとログのみで再生成しない。45 分後にトークン失効 → WS が切れる → reconnect loop で `create_ws_access_token` が再発行されるので結果的には復旧するが、その間 TP/SL イベントを取りこぼす。**`extend` が NG なら明示的に `_app.close()` を呼んで reconnect を早める**。

---

## 8. ログ・観測性

### 8.1 [Mid] `ConsoleLogger` が単に print
[apps/gmo_bot/infra/logging/logger.py:24-31](../apps/gmo_bot/infra/logging/logger.py#L24-L31)

stdout 直書き。本番（Cloud Run / GKE）では Cloud Logging に拾われるが、structured JSON でないため severity フィールドや trace_id が連携しない。`google-cloud-logging` の `StructuredLogHandler` を使う or 自前で JSON 1 行に整形する。

### 8.2 [Mid] Slack 送信失敗時に message 全文を warn に含める
[apps/gmo_bot/infra/alerting/slack_notifier.py:339-342](../apps/gmo_bot/infra/alerting/slack_notifier.py#L339-L342)

エラーログに同じ message を埋めると Cloud Logging のクォータを食う。`message_preview = message[:200]` 等に切る。

### 8.3 [Low] `logger.warn` / `logger.error` だけで `logger.debug` が存在しない
[apps/gmo_bot/app/ports/logger_port.py](../apps/gmo_bot/app/ports/logger_port.py)

トレース用のレベルが無いため、現状の info ログがすでに verbose。`debug` の追加を検討。

### 8.4 [Low] cron スケジュール文字列がコメントだけで実体無し
[apps/gmo_bot/infra/scheduler/cron_cycle.py:25](../apps/gmo_bot/infra/scheduler/cron_cycle.py#L25)

`schedule = "* * * * *"` を `logger.info` に渡しているだけ。`from croniter import croniter` 等を導入する設計（環境変数で cron 式を変更可能）ならば残しても良いが、現状はマジック文字列。

---

## 9. コード品質・命名

### 9.1 [Mid] 巨大ファイル
| ファイル | 行数 |
| --- | --- |
| [adapters/persistence/firestore_repo.py](../apps/gmo_bot/adapters/persistence/firestore_repo.py) | 789 |
| [app/usecases/close_position.py](../apps/gmo_bot/app/usecases/close_position.py) | 847 |
| [infra/bootstrap.py](../apps/gmo_bot/infra/bootstrap.py) | 652 |
| [app/usecases/run_cycle.py](../apps/gmo_bot/app/usecases/run_cycle.py) | 582 |
| [infra/alerting/slack_notifier.py](../apps/gmo_bot/infra/alerting/slack_notifier.py) | 437 |
| [app/usecases/open_position.py](../apps/gmo_bot/app/usecases/open_position.py) | 391 |
| [adapters/execution/gmo_margin_execution.py](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py) | 399 |

特に `close_position.py:691-847` (`close_position` 本体 156 行) と `run_cycle.py:186-583` (`run_cycle` 本体 397 行) は責務分割の余地が大きい:
- 「open trade あり」ブロック → `handle_open_trade` 関数
- 「TAKE_PROFIT latch」処理 → `latch_take_profit` 関数
- 「protective stop reconcile + close」 → `close_or_reconcile` 関数

### 9.2 [Mid] 同一ヘルパの重複実装
| ヘルパ | 重複箇所 |
| --- | --- |
| `_to_float(value)` | bootstrap.py:290, close_position.py:61, gmo_margin_execution.py:359, exit_order_monitor.py:299, protective_exit_orders.py:20, daily_trade_summary.py:365 |
| `_to_str(value)` | close_position.py:74 ほか |
| `_as_dict(value)` | bootstrap.py:297, daily_trade_summary.py:373 |
| `_decimal_str(value)` | gmo_api_client.py:291, gmo_margin_execution.py:392 |

`shared/utils/coercion.py` のようなモジュールに集約。

### 9.3 [Mid] マーカー文字列の重複定義
[apps/gmo_bot/app/usecases/run_cycle.py:52-67](../apps/gmo_bot/app/usecases/run_cycle.py#L52-L67) と [apps/gmo_bot/infra/alerting/slack_notifier.py:15-26](../apps/gmo_bot/infra/alerting/slack_notifier.py#L15-L26) で `_EXECUTION_ERROR_SKIP_MARKERS` / `_MARKET_DATA_MAINTENANCE_MARKERS` が完全コピー。

### 9.4 [Mid] `PAIR_SYMBOL_MAP` の重複
[apps/gmo_bot/adapters/market_data/ohlcv_provider.py:15](../apps/gmo_bot/adapters/market_data/ohlcv_provider.py#L15) と [apps/gmo_bot/adapters/execution/gmo_margin_execution.py:23](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L23) で同じ map。

### 9.5 [Low] 未使用 import
- [apps/gmo_bot/adapters/execution/gmo_margin_execution.py:4](../apps/gmo_bot/adapters/execution/gmo_margin_execution.py#L4) — `from dataclasses import asdict` が未使用。

### 9.6 [Low] `reports.py` で `_send` を直接叩く
[apps/gmo_bot/reports.py:116-118](../apps/gmo_bot/reports.py#L116-L118)

`SlackNotifier._send` を直接呼んで `# noqa: SLF001` を付けている。public な `send_plain_text` メソッドを追加する方が筋。

### 9.7 [Low] `to_error_message` の実装が冗長
[apps/gmo_bot/app/usecases/usecase_utils.py:14-17](../apps/gmo_bot/app/usecases/usecase_utils.py#L14-L17)

```python
def to_error_message(error):
    if isinstance(error, BaseException):
        return str(error)
    return str(error)
```

両分岐とも `str(error)`。`return str(error)` の 1 行で十分。

### 9.8 [Low] `to_error_message` のシグネチャが「`Exception | BaseException | object`」と redundant
`Exception` ⊆ `BaseException` ⊆ `object`。`Any` か `object` に。

### 9.9 [Low] `_build_strategy_execution_bridge` の `reference_price` を `del` で破棄
[apps/gmo_bot/app/usecases/run_cycle.py:138-142](../apps/gmo_bot/app/usecases/run_cycle.py#L138-L142)

未使用の引数を `del` で消すパターンが Pythonic でない。シグネチャから外すか、`_reference_price` にリネーム。

### 9.10 [Low] `bootstrap._mark_pause_refresh_needed` / `_mark_runtime_refresh_needed` のロジックが対称
中身がほぼ同じ。デコレータ化 or 共通関数化。

---

## 10. セキュリティ

### 10.1 [Mid] API シークレットを bytes でメモリ常駐
[apps/gmo_bot/adapters/execution/gmo_api_client.py:27](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L27)

`self.api_secret = api_secret.encode("utf-8")` で起動から終了までメモリ保持。プロセスダンプで即漏洩。HMAC 計算は ms オーダーで終わるので、**毎回 `self._raw_secret.encode("utf-8")` してから直後に `del encoded` のパターン**にしても性能影響は無視できる。優先度は低い。

### 10.2 [Low] HMAC 署名にナンス無し
[apps/gmo_bot/adapters/execution/gmo_api_client.py:261-265](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L261-L265)

タイムスタンプのみで replay 防止は GMO 側に依存。これは GMO API 仕様の制約なので変更不可。記録のみ。

### 10.3 [Low] `_build_gmo_error_message` がエラー時 payload 全体を埋め込み
[apps/gmo_bot/adapters/execution/gmo_api_client.py:296-311](../apps/gmo_bot/adapters/execution/gmo_api_client.py#L296-L311)

`return f"GMO API error status={payload.get('status')}: {payload}"` で payload を全文 stringify。`payload` に order id 等が入る可能性があるが秘匿情報ではない。OK だが、`messages` 解析失敗時の fallback でも payload を切り詰めると安全。

---

## 11. テスト容易性

### 11.1 [Mid] `bootstrap()` が単一の巨大クロージャ
[apps/gmo_bot/infra/bootstrap.py:87-652](../apps/gmo_bot/infra/bootstrap.py#L87-L652)

state（`runtime_specs`, `model_contexts`, `failure_streaks_by_model`, ロック, スレッド等）が全部クロージャに閉じ込められているため、ユニットテストでパスを切り出して呼べない。`AppRuntime` をクラス化し、各内部関数をメソッド化するのが正攻法。

### 11.2 [Mid] `FirestoreRepository` のキャッシュ状態がコンストラクタで初期化
[apps/gmo_bot/adapters/persistence/firestore_repo.py:140-164](../apps/gmo_bot/adapters/persistence/firestore_repo.py#L140-L164)

キャッシュフィールドが 7 個。テストで invalidation を再現するのが面倒。`_TradeCache` クラスに分離して inject。

### 11.3 [Mid] `now_provider` が `RunCycleDependencies` にしかなく、`close_position` / `open_position` / `protective_exit_orders` は `now_iso()` 直叩き
[apps/gmo_bot/app/usecases/usecase_utils.py:10-11](../apps/gmo_bot/app/usecases/usecase_utils.py#L10-L11)

タイムスタンプの決定論性を確保する単体テストが書きにくい。**`now_provider: Callable[[], datetime]` を `Dependencies` 全部に持たせる**。

---

## 12. 雑多な気付き

### 12.1 [Low] `domain/strategy/risk_constants.py` が 4 行
[apps/gmo_bot/domain/strategy/risk_constants.py](../apps/gmo_bot/domain/strategy/risk_constants.py) は `MIN_STOP_DISTANCE_PCT` のみ。`domain/strategy/__init__.py` か別ファイルに統合してもよい。

### 12.2 [Low] `domain/indicators/` が空
[apps/gmo_bot/domain/indicators/__init__.py](../apps/gmo_bot/domain/indicators/__init__.py) が 1 行のみで中身無し。dex_bot の indicators を借用しているため、空ディレクトリは削除推奨。

### 12.3 [Low] `infra/reporting/__init__.py` が空ファイル
import で参照される予定がないなら削除可。

### 12.4 [Low] `reports.py` のヘッドラインを stdout に直接 print
[apps/gmo_bot/reports.py:99-100](../apps/gmo_bot/reports.py#L99-L100)

CLI ツールなので意図的だと思われるが、コーディング規約上 `logger.info` 経由が一貫する。

### 12.5 [Low] `paper_execution.get_mark_price` のデフォルトが `20_000.0` 固定
[apps/gmo_bot/adapters/execution/paper_execution.py:118](../apps/gmo_bot/adapters/execution/paper_execution.py#L118)

`SOL/JPY` の現状価格と乖離。テストの再現性を確保したいなら `now_provider` 並みに inject 可能にする。

### 12.6 [Low] `build_trade_id` の `safe_model_id` 変換で衝突可能性
[apps/gmo_bot/domain/utils/time.py:51-54](../apps/gmo_bot/domain/utils/time.py#L51-L54)

`foo.bar` と `foo_bar` が同じ trade_id プレフィックスを生む。実 model_id は alnum + 数字なので踏まないが、防御的にハッシュサフィックスを足すと安全。

### 12.7 [Low] `ohlcv_provider` のキャッシュキー
[apps/gmo_bot/adapters/market_data/ohlcv_provider.py:65](../apps/gmo_bot/adapters/market_data/ohlcv_provider.py#L65)

`cache:gmo:ohlcv:{symbol}:{interval}:{date_token}` で TTL 30 秒。同じ日付トークンに同じ rows を毎回上書き保存しており Redis 帯域は問題ないが、`date_token` がまだ閉じきっていない当日についてもキャッシュされる点に注意（再取得しない 30 秒間に新しい確定足が出る）。`_is_confirmed_bar` で fetch 後にフィルタしているので結果整合性は問題ないが、コメントを足すと意図が伝わる。

### 12.8 [Low] `ohlcv_provider._aggregate_bars` が「1 本だけ存在するバケット」を捨てる
[apps/gmo_bot/adapters/market_data/ohlcv_provider.py:209-210](../apps/gmo_bot/adapters/market_data/ohlcv_provider.py#L209-L210)

2h 集約で「1h バーが片方しか取得できていない」場合に捨てる。妥当だが、当日最新足が必ず欠落することの理由をコメント化。

### 12.9 [Low] `_should_persist_run_record` のロジックが暗黙
[apps/gmo_bot/app/usecases/run_cycle.py:176-183](../apps/gmo_bot/app/usecases/run_cycle.py#L176-L183)

「SKIPPED は基本保存しないが、execution_error と maintenance だけ保存する」というルールは Slack 通知側のフィルタと一貫しているが、コメントが無いと読み手が一度ファイル横断する必要あり。

### 12.10 [Low] `bootstrap.STALE_CYCLE_ALERT_MINUTES = 10` のハードコード
[apps/gmo_bot/infra/bootstrap.py:40](../apps/gmo_bot/infra/bootstrap.py#L40)

GMO の保守ウィンドウは 60 分超になることもある。グローバル制御（`control/global`）の `stale_cycle_threshold_minutes` 等で動的に上書きできるようにする。

---

## 13. 優先度別アクションリスト

### 即着手すべきもの（運用事故の確率を直接下げる）
- 1.3 補助スレッドの try/except 追加（dead daemon thread 防止）
- 1.1 `_clear_open_trade_state` の例外を可視化
- 1.5 `requests.Session` の thread-safety 対策
- 1.6 HTTP リトライ層の導入（少なくとも GET と cancel）
- 1.4 `close_position` 部分約定ループの最大反復数

### 次に着手すべきもの（保守性 / 観測性）
- 1.7 Firestore 直近 closed の Transaction 化
- 2.3 `_active_model_contexts` のスナップショット化
- 7.1 WS stale アラート
- 8.1 構造化ロギング
- 1.18 `config_fingerprint` の安定化

### 整備系（コード品質）
- 5.2 dex_bot 依存の `shared/` 化
- 9.1 巨大ファイルの責務分割
- 9.2-9.4 ヘルパ・定数の重複排除
- 4.1 金額計算の Decimal 移行（影響範囲広）

### 後回しでよいもの
- 10.x セキュリティ（現状致命的でない）
- 12.x 軽微な命名 / 配置

---

## 付録: レビュー対象外（明示）

- `apps/gmo_bot/domain/strategy/models/ema_trend_pullback_15m_v0.py` — 売買シグナルロジック本体は別途レビュー予定。
- `apps/gmo_bot/app/reporting/*` — レポーティングは本筋でないため軽い確認のみ。問題は見つからず。
- 各種 `__init__.py` — 空 or import re-export のみ。
