---
name: trader-xau
description: >-
  Gold Signals. Opening-range breakout signals for gold (XAUUSD), long only,
  read live from the user's open MetaTrader 5 terminal with no delay: entry,
  stop and target as prices and dollar distances, and position size in lots for
  the user's balance. Replays the rule on the terminal's own history. Reads
  only and never places an order. Use when the user says "XAU", "XAUUSD",
  "gold", "طلا", "signal on gold", "how many lots", or "backtest gold".
argument-hint: "[price | check | backtest] [--balance 1000] [--max-loss 10]"
---

# trader-xau

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
On Windows, use `python` if `python3` is missing. MT5 must be open and logged
in, and `pip install MetaTrader5` must have run.

```bash
python3 "${CLAUDE_SKILL_DIR}/xau.py" price
python3 "${CLAUDE_SKILL_DIR}/xau.py" check --balance 1000 --max-loss 10
python3 "${CLAUDE_SKILL_DIR}/xau.py" backtest --max-loss 10
```

Other flags: `--symbol XAUUSD.st` (any MT5 symbol, such as `DowJones.st`),
`--leverage 200` (default: the account's).
Exit code 0 means a signal (or a finished price or backtest), 1 means no trade,
2 means MT5 could not supply data.

## Telegram

```bash
python3 "${CLAUDE_SKILL_DIR}/xau.py" telegram-test
python3 "${CLAUDE_SKILL_DIR}/xau.py" watch --balance 50000 --max-loss 100
```

`watch` looks every 30 seconds (`--every`) while MT5 and the computer stay on,
and posts each signal once, then its stop, target or closing reminder. The token
and chat id live in `~/.trader-xau/telegram.json` as `{"token": "...", "chat_id":
"@channel or -100..."}`, or in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The
user types the token there. Never ask for it in chat, and never print it.

## Scheduled analysis

```bash
python3 "${CLAUDE_SKILL_DIR}/analysis.py" preview
python3 "${CLAUDE_SKILL_DIR}/analysis.py" run
```

`run` posts a Persian gold analysis (M5 and M15 levels and scenarios, no signals)
Monday to Friday from 15:30 to 00:30 Iran time. It posts again
after a random 20 to 30 minutes, or sooner when a closed M5 bar activates or
breaks a scenario, or when someone in the group sends only `gold`, `/gold` or `گلد`
(one answer a minute). `preview` prints one without posting. Each message may also
have GLD's opening range from Yahoo (the Day Trader skill's data) and red,
high-impact US events from about 100 minutes before they happen. News comes from
forexfactory.live in Persian, then Forex Factory's feed, then TradingView, in
English, with New York times and a countdown to each event. A new gold headline from TradingView appears only when both Forex
Factory sources fail. TradingView's gauges move the bias and are named only in every third
message or when they oppose the main direction. A source that fails is left out.

Messages also carry yesterday's high and low, today's Asia and London sessions,
round numbers, and plain sentences about the M5 structure and reaction areas,
from fair value gaps and Fibonacci at the 5-1-1 setting (`smc.py`), without their
exact numbers, and one general sentence on whether they agree. From 10 minutes before a red event until 5 minutes
after, scenarios stop and one waiting message goes out. RSI, moving averages,
price action, the trend line, structure and TradingView's gauges vote on M5, and
one sentence gives the result in five states, from strong up to strong down.

`supervisor.py` runs both bots, restarts one that stops, and reports outages to
Telegram. `start-all.cmd`, behind the desktop icon, opens Telegram and MT5 and
starts it. A second copy exits by itself.

## The rule

The opening range is the first 15 minutes of the New York session (9:30 to
9:45). A signal is the first closed 5-minute bar above the range high, up to
15:30. The stop is the range midpoint and the target is 2R. Close by 15:55.

Times assume the broker's server clock runs 7 hours ahead of New York, which
was checked on ePlanet-MT5. On another broker, check `BROKER_AHEAD_NY` first.

## The fast rule

`--rule fast` works with `check`, `backtest` and `watch`, and gives 1 to 3 buy or
sell signals per block (to 11:45, to 13:45, to 15:30 New York), starting at
10:45. A signal is a closed 5-minute bar beyond the last 24 bars' high or low,
at least 30 minutes after the last one, with one trade open at a time. A block
with no signal relaxes to a 1-bar break in its last 30 minutes, and takes its
last bar's direction if none comes. Say these weak entries exist. The stop is 1
ATR(14), the target 2 ATR, and a trade closes after an hour or at 15:55.
`--slippage` tests a dollar cost per trade in `backtest`.

## How to answer

- Keep it short, in the user's language: the status, then entry, stop, target and lots.
- Ask for the balance and max loss if the user has not given them. Do not choose
  a max loss for them.
- Report backtest losses as plainly as gains. A backtest does not predict results.
- This is a mechanical rule, not investment advice. Never place an order from it.
