# XAU Gold Analysis & Signal Bot (MetaTrader 5 + Telegram)

**English** | [فارسی](README.fa.md)

A Python automation that reads live gold (XAUUSD) prices from a running **MetaTrader 5** terminal, writes a market analysis in Persian, and posts it to a **Telegram** channel every 20–30 minutes during the US session. It also generates rule-based breakout signals with position sizing, back-tests the rules on the terminal's own history, and watches selected public Telegram channels.

> It only **reads** market data. It holds no broker password and contains no order-placing code.

## What it does

| Part | File | Job |
|---|---|---|
| Signals & back-test | `xau.py` | Opening-range breakout rule (and a faster variant): entry, stop, target, lot size for a given balance and max loss; replays the rule on history |
| Scheduled analysis | `analysis.py` | Persian gold analysis, Mon–Fri 15:30–00:30 Iran time, re-posted every 20–30 min or when price breaks a scenario; answers `gold` / `/gold` in the group |
| Market structure | `smc.py` | Swing structure, fair value gaps, Fibonacci levels, Asia/London session ranges, round numbers |
| Channel watcher | `channels.py` | Checks public `t.me/s/` pages and posts a one-line alert with a link when a watched channel goes live or posts a signal or analysis |
| Supervisor | `supervisor.py` | Runs the bots, restarts one that stops, reports outages to Telegram |
| Launcher | `start-all.cmd` | Opens Telegram and MT5 if needed, then starts the supervisor (Windows) |

### Analysis inputs
- M5 and M15 bars from MT5: yesterday's high/low, session ranges, structure, FVGs, Fibonacci
- Indicator vote on M5 (RSI, moving averages, price action, trend line, structure, TradingView gauges) → one of five bias states
- High-impact US economic events (Forex Factory, then TradingView as fallback); scenarios pause from 10 minutes before a red event until 5 minutes after
- GLD opening range from Yahoo Finance
- Any source that fails is simply left out

### The signal rule
The opening range is the first 15 minutes of the New York session (9:30–9:45). A signal is the first closed 5-minute bar above the range high, up to 15:30. Stop = range midpoint, target = 2R, flat by 15:55. A `--rule fast` mode gives 1–3 signals per two-hour block using ATR-based stops.

## Setup

```bash
pip install -r requirements.txt          # MetaTrader5 (Windows, MT5 terminal open and logged in)
python xau.py price                      # live quote
python xau.py check --balance 1000 --max-loss 10
python xau.py backtest --max-loss 10
python analysis.py preview               # print one analysis without posting
```

Telegram settings live **outside the repository** in `~/.trader-xau/telegram.json` (see `examples/telegram.example.json`) or in the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables. Channels to watch go in `~/.trader-xau/channels.json` (see `examples/channels.example.json`).

## How to use (step by step)

**1. Prepare the computer** (Windows)
- Install Python 3.12 and MetaTrader 5. Open MT5, log in to your broker account, and make sure gold is in Market Watch.
- In this folder run `pip install -r requirements.txt`.
- The default symbol is `XAUUSD.st` (ePlanet). If your broker uses another name, such as `XAUUSD`, pass `--symbol XAUUSD` to `xau.py` and change `SYMBOL` at the top of `analysis.py`.

**2. Create the Telegram bot**
- In Telegram, message **@BotFather**, send `/newbot` and copy the token it gives you.
- Add the bot to your channel or group as an **admin** that can post messages.
- Copy `examples/telegram.example.json` to `~/.trader-xau/telegram.json` (on Windows: `C:\Users\<you>\.trader-xau\telegram.json`) and put in your token and channel (`@channel_name` or the `-100…` id).
- Run `python xau.py telegram-test`. A test message should appear in the channel.

**3. Check it before going live**
- `python xau.py price` shows the live gold price from MT5.
- `python analysis.py preview` prints one full analysis without posting it.

**4. (Optional) Channel watcher**
- To get an alert when other public Telegram channels go live or post a signal, copy `examples/channels.example.json` to `~/.trader-xau/channels.json` and list the channels.
- `python channels.py scan` shows how recent posts would be labelled, without posting.

**5. Start everything**
- Double-click `start-all.cmd`. It opens Telegram and MT5 if they are closed, then starts the supervisor in the background, which runs the analysis bot and the channel watcher and restarts them if they stop.
- Or run `python supervisor.py` in a terminal.
- Analyses are posted Monday to Friday, 15:30–00:30 Iran time. Logs are in `~/.trader-xau/`.
- In the group, anyone can send `gold`, `/gold` or `گلد` to get a fresh analysis (at most once a minute).

**6. Signals and back-test (on demand)**
- `python xau.py check --balance 1000 --max-loss 10` → today's signal with entry, stop, target and lot size.
- `python xau.py backtest --max-loss 10` → how the rule did on past data.
- `python xau.py watch --balance 1000 --max-loss 10` → posts each signal and its result to Telegram.

**Keep in mind**
- The computer and MT5 must stay on. If MT5 data stops, the bot posts one notice in the channel and carries on when data returns.
- Never share or commit `telegram.json`; it holds your bot token.

## Tests

```bash
python -m pytest -q        # 90 tests, MT5 and network calls are stubbed
```

## Notes
- Built as a Claude Code skill (`SKILL.md`), with Python's standard library plus the `MetaTrader5` package.
- Broker server time is assumed to run 7 hours ahead of New York; check `BROKER_AHEAD_NY` for your broker.

## Disclaimer
This is a mechanical, educational tool, not investment advice. Back-test results do not predict future results. Use at your own risk.

---
Author: **Naser Hosseinpour**
