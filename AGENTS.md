# AI Implementation Guidelines

このリポジトリでAIが実装する際の基本方針:

3. あまり意味のない関数分割はしない。
4. 1回しか使わない薄いラッパー関数は作らない。
6. 変更時はテストを追加または更新し、意図した挙動を明示する。
7. 仕様を変える場合は、先に影響範囲（設定・実行・research）を確認する。

運用メモ:
- VPS へ手動接続する時は `ssh root@160.251.203.188` を使わず、必ず `make ssh-gmo-vps` を使う。
- VPS 上の確認系コマンドも `make gmo-vps-ps` / `make gmo-vps-gmo-logs` / `make gmo-vps-dex-logs` を優先する。
