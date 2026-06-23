# Operations Log - 2026-06-23

This note captures the trading bot checks and changes performed on 2026-06-23.

## Current Runtime Setup

- EC2 host: `ec2-13-233-88-163.ap-south-1.compute.amazonaws.com`
- SSH user: `ubuntu`
- SSH key used locally: `C:\Users\mpmin\Downloads\trading_bot.pem`
- EC2 app directory: `/opt/ai_trading_agent`
- EC2 bot service: `trading-bot.service`
- EC2 bot timer: `trading-bot.timer`
- EC2 bot virtualenv: `/opt/ai_trading_agent/.venv`
- Local virtualenv used for checks: `.venv`

The EC2 systemd service runs:

```bash
/opt/ai_trading_agent/.venv/bin/python -m trading_bot.cli run --config /opt/ai_trading_agent/config.yaml
```

`.venv-1` exists locally, but it is not used by the EC2 bot service.

## Dry-Run Status

The deployed EC2 config was checked and has:

```yaml
broker:
  live_trading: false
market:
  poll_interval_seconds: 5
  square_off_time: "15:15"
```

The bot timer was enabled and scheduled to start at:

```text
2026-06-23 09:14:00 IST
```

The strategy includes a `time_after` filter set to `09:45`, so new entries should not be expected before `09:45 IST`.

## Logging And Storage

Daily bot decisions and dry-run trade activity are written to the EC2 journal file:

```text
/opt/ai_trading_agent/data/trade_journal.jsonl
```

This journal records events such as:

- `bot_started`
- `signal`
- `order_skipped`
- `order_placed`
- `position_closed`
- `square_off_started`

DynamoDB is used for Zerodha Kite postback events only:

```text
ai-trading-agent-KitePostbackTable-1PU5KKZLF71Z
```

Access tokens are stored in AWS Secrets Manager:

```text
arn:aws:secretsmanager:ap-south-1:975050253457:secret:KiteSecret-tSjUSdVntCZR-2bin3w
```

## Zerodha Postbacks

Yesterday's live orders generated 7 postback records in DynamoDB on 2026-06-22.

Observed postback times matched the live order times:

- Around `09:41 IST`
- Around `09:50 IST`

However, all existing DynamoDB postback records had:

```json
{}
```

in the decoded `payload` field.

## Temporary Postback Debug Logging

To diagnose why the decoded postback payload was empty, the Lambda postback handler was updated to store raw request details for new postbacks.

Updated files:

- `lambda_src/lambda_app.py`
- `lambda_app.py`

New DynamoDB fields added for postback records:

- `raw_body`
- `headers`
- `query_string_parameters`
- `is_base64_encoded`
- `request_context`

The existing `payload` field is still stored unchanged.

This is temporary debugging data and can be removed after confirming Zerodha's actual postback shape.

## SAM Deployment

The Lambda change was deployed with SAM to the existing CloudFormation stack:

```text
Stack: ai-trading-agent
Region: ap-south-1
Status: UPDATE_COMPLETE
```

SAM deploy updated:

- `KitePostbackFunction`
- `KiteCallbackFunction`
- `KiteLoginUrlFunction`
- `KiteHttpApi`

Postback URL:

```text
https://7m0198mfm4.execute-api.ap-south-1.amazonaws.com/prod/kite/postback
```

CloudFormation is used because SAM deploys the resources defined in `template.yaml` as one managed stack.

## Backtest Result

A fresh EC2 backtest was run with the deployed config:

```text
Backtest interval: 5minute
Days requested: 30
Trading days tested: 20
Trades: 38
Wins: 24
Losses: 14
Win rate: 63.16%
Total P&L: 1489.29
```

The strategy produced at least one trade on 18 of 20 tested trading days, giving a rough historical trade-frequency estimate of:

```text
18 / 20 = 90%
```

This is only a historical estimate, not a guarantee for any given day.

## Automated Tests

The project has tests under `tests/`.

`tests/test_strategies.py` covers:

- SMA crossover
- Opening range breakout
- Open-high/open-low signals
- RSI mean reversion
- Composite strategy agreement
- Time, volume, candle body, and VWAP filters
- EMA crossover
- MACD trend
- Volume spike
- Support/resistance breakout

`tests/test_risk.py` covers:

- Exit allowed after daily entry limit is reached
- Long trailing stop
- Short trailing stop

`tests/test_journal.py` covers:

- Candle classification
- Indicator snapshots
- Learning report summary for a closed trade

`pytest` is only needed to run automated tests. It is not required to run the trading bot.

## Local Notes

SAM build initially hit Windows/OneDrive permission issues with existing build and temp directories. The build succeeded after allowing SAM to run with elevated permissions.

Local SAM artifacts created during deployment:

- `.aws-sam-deploy-build/`
- `.sam-tmp/`
- `build.toml`

These are deployment/build artifacts, not runtime bot logic.
