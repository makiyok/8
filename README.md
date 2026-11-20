# 八幡平収穫体験

農産物の生産者が、大根抜き・ミカン狩り・ブルーベリー狩りなどの「収穫する権利（収穫体験）」を販売するアプリ。
作物の種類は限定しない（生産者が作物名を自由に入力する。12の分類はイラストと絞り込み用）。
時期・持ち帰りの量・値段・1日の定員は、生産者が体験ごとに決める。支払いは Stripe Checkout（カード決済）。

## Stripe の設定（開発）

1. `.env.example` を `.env` にコピーし、`STRIPE_SECRET_KEY`（sk_test_...）を入れる
2. Stripe CLI で Webhook を転送し、表示された whsec_... を `STRIPE_WEBHOOK_SECRET` に入れる
   `stripe listen --forward-to localhost:8001/stripe/webhook`
3. テストカード `4242 4242 4242 4242`（期限は未来の日付、CVC は任意）で支払う

予約の流れ：予約する → 支払い待ち（35分だけ定員を押さえる）→ Stripe で支払う → Webhook か完了ページで確定。
来園日の前日までの取り消しは全額返金。期限切れや取り消しのあとに支払いが届いた場合も自動で返金する。

## 起動

```bash
uv sync
uv run flask db upgrade
uv run flask seed        # サンプル（farm@example.com / guest@example.com、パスワード password1234）
uv run flask run --debug # http://127.0.0.1:8001
```

## テスト

```bash
uv run pytest -q
uv run ruff check .
```

## 構成

- `harvest/views/` … auth（会員登録・生産者登録・ログイン）、shop（トップ・検索・詳細）、producer（出品管理）、reservations（予約）
- `harvest/services/booking.py` … 予約の作成と取り消し（金額と定員はサーバーで計算）
- `harvest/uploads.py` … 写真の検査と作り直し（EXIF を消す）
- `harvest/static/img/crops/` … 分類ごとのイラスト（写真が無いときにも使う）

## セキュリティで決めていること

- 他人の体験・予約は、取得したあとに所有者を確かめて 403。非公開の体験は本人以外 404
- CSRF はアプリ全体で有効。状態を変える操作はすべて POST
- 金額はフォームから受け取らない。定員チェックは行ロック（SQLite は BEGIN IMMEDIATE）の中で行う
- 1人が同時に持てる予約は10件まで
- ログインは15分で5回失敗するとロック。`next` は `/\evil.com` も含めて外部へ飛ばさない
- 写真は中身を Pillow で確かめ、JPEG に作り直して EXIF（GPS など）を消す。ファイル名は UUID
- 本番は `APP_ENV=production`。SECRET_KEY と TRUSTED_HOSTS が無いと起動しない。Cookie は Secure・SameSite=Lax
- CSP・X-Frame-Options・nosniff を全レスポンスに付ける（外部 CDN は使わない）

## 公開（Render）

`render.yaml` に Web サービスと PostgreSQL（どちらも無料プラン）をまとめてある。

1. Render で New → Blueprint → このリポジトリを選び、Apply
2. Web サービスの Environment で `STRIPE_SECRET_KEY`（sk_test_...）を入れる
3. Stripe のダッシュボード（テストモード）→ Webhook に `https://<公開URL>/stripe/webhook` を登録
   （イベント: `checkout.session.completed` `checkout.session.expired` `checkout.session.async_payment_succeeded` `checkout.session.async_payment_failed`）。
   表示された whsec_... を `STRIPE_WEBHOOK_SECRET` に入れる
4. サンプルのアカウント（farm@example.com / guest@example.com）のパスワードは、Environment の `SEED_PASSWORD`

起動のたびに `flask db upgrade` と `flask seed`（サンプルが無いときだけ作る）を実行してから gunicorn が動く。
無料プランの注意: 写真はサーバーのディスクに保存するので、再起動や更新で消える。無料のデータベースには期限がある。

`requirements.txt` は `uv export --no-dev --no-hashes --no-emit-project -o requirements.txt` で作る（パッケージを変えたら作り直す）。
