# Zerodha Intraday Trading Bot

A Python intraday trading bot scaffold for Zerodha Kite Connect. It supports dynamic stock watchlists, pluggable strategies, risk checks, dry-run execution, and optional live MIS order placement.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item config.sample.yaml config.yaml
```

Fill `.env` with your Kite Connect credentials and edit `config.yaml` with the symbols, quantities, and strategy parameters you want.

## Generate A Kite Access Token

```powershell
python -m trading_bot.cli login-url
python -m trading_bot.cli generate-token --request-token <request_token_from_redirect>
```

Put the returned access token in `.env` as `KITE_ACCESS_TOKEN`.

## Deploy Kite Redirect And Postback URLs To AWS

This project includes an AWS SAM template that creates:

- API Gateway HTTP API
- `/kite/login` endpoint that returns your Zerodha login URL
- `/kite/callback` endpoint for the Kite redirect URL
- `/kite/postback` endpoint for Kite order postbacks
- AWS Secrets Manager secret for Kite credentials and the daily access token
- DynamoDB table for raw postback events

Install and configure prerequisites:

```powershell
aws configure
sam --version
```

Deploy:

```powershell
$py312 = 'C:\Users\mpmin\AppData\Local\Programs\Python\Python312'
$env:Path = "$py312;$py312\Scripts;$env:Path"
sam build
sam deploy --guided `
  --parameter-overrides KiteApiKey=<your_api_key> KiteApiSecret=<your_api_secret>
```

The SAM Lambda package is built from `lambda_src/` so it does not include the local trading bot virtualenv or Windows-only `kiteconnect` dependencies. Docker is not required for this build path.

During guided deploy, allow SAM to create IAM roles. After deploy finishes, SAM prints these outputs:

- `KiteRedirectUrl`: use this as the Kite Connect redirect URL.
- `KitePostbackUrl`: use this as the Kite Connect postback URL.
- `KiteLoginUrlEndpoint`: open this endpoint to get the actual Zerodha login URL.

Daily login flow after deployment:

```powershell
curl https://<api-id>.execute-api.<region>.amazonaws.com/prod/kite/login
```

Open the returned `login_url`. After you log in, Zerodha redirects to `/kite/callback`, and the Lambda saves the new access token into AWS Secrets Manager.

Note: Lambda is a poor fit for an all-day intraday polling loop because each invocation has a maximum runtime. Use Lambda/API Gateway for login, callback, and postbacks; run the trading engine on EC2 during market hours.

## Run The Trading Engine On EC2

The EC2 runner uses the same AWS Secrets Manager secret that the Lambda callback updates each morning. When `KITE_SECRET_NAME` is set, the bot reads Kite credentials from Secrets Manager. When it is not set, the bot falls back to local `.env` credentials.

Find the deployed secret name:

```powershell
aws cloudformation describe-stack-resource `
  --stack-name ai-trading-agent `
  --logical-resource-id KiteSecret `
  --query StackResourceDetail.PhysicalResourceId `
  --output text `
  --region ap-south-1
```

Attach the stack-created instance profile to the EC2 instance. The profile is printed by SAM as `TradingBotEc2InstanceProfileName` and allows the instance to read only the Kite secret from this stack.

On the EC2 instance:

```bash
sudo timedatectl set-timezone Asia/Kolkata
sudo mkdir -p /opt/ai_trading_agent
sudo chown ubuntu:ubuntu /opt/ai_trading_agent
cd /opt/ai_trading_agent
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Copy the project files and `config.yaml` to `/opt/ai_trading_agent`. Then install the systemd service and timer:

```bash
sudo cp deploy/trading-bot.service /etc/systemd/system/trading-bot.service
sudo cp deploy/trading-bot.timer /etc/systemd/system/trading-bot.timer
sudo sed -i 's/REPLACE_WITH_KITE_SECRET_NAME/<kite-secret-name>/g' /etc/systemd/system/trading-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot.timer
```

Check status and logs:

```bash
systemctl list-timers trading-bot.timer
sudo systemctl status trading-bot.service
journalctl -u trading-bot.service -f
```

The bot also writes structured trade journal records to:

```text
/opt/ai_trading_agent/data/trade_journal.jsonl
```

Each line is one JSON event for later analysis, including signals, skipped orders, placed orders, exits, and square-off events.

After market close, generate a learning report from the journal:

```bash
python -m trading_bot.cli learning-report --journal /opt/ai_trading_agent/data/trade_journal.jsonl --date YYYY-MM-DD
```

The report summarizes closed trades, open trades, gross P&L, symbol/candle performance, max favorable/adverse movement, and the indicator snapshot recorded at entry.

## Run In Dry-Run Mode

```powershell
python -m trading_bot.cli run --config config.yaml
```

`live_trading` is `false` in the sample config, so orders are logged but not sent.

## Enable Live Trading

Set this only after dry-run validation:

```yaml
broker:
  name: zerodha
  live_trading: true
```

Live intraday equity orders use Zerodha MIS market orders through Kite Connect.

## Strategies

Supported strategy names:

- `sma_crossover`: buys when fast SMA crosses above slow SMA and sells/exits when it crosses below.
- `sma_trend_filter`: returns a bullish or bearish trend confirmation based on fast/slow SMA.
- `opening_range_breakout`: buys above the opening range high and sells below the opening range low after the configured opening window.
- `rsi_mean_reversion`: buys when RSI is oversold and exits after RSI recovers.
- `composite`: combines multiple strategies using `all` or `majority` agreement.

Each stock in `watchlist` can use different strategy parameters.

## Safety Defaults

- Dry-run mode by default.
- One open position per symbol.
- Per-trade stop loss and target.
- Optional trailing stop loss.
- Daily trade count limit.
- Intraday square-off time.
