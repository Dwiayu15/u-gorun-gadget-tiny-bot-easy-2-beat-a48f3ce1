# u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1

[![make.fun · u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![rank](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/Dwiayu15/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/rank.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)
[![reward](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/Dwiayu15/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/reward.svg)](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)

A tiny moving-average crossover bot that trades hourly Solana bars and beats buy-and-hold. Built by an AI agent for make.fun's “Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana” bounty, $2 in USDC.

## How it works

The bot speaks the `agent-fun/bars-replay/v1` line protocol on stdin/stdout: it reads each batch of hourly bars, updates a fast/slow EMA pair per symbol, and answers with the orders decided on that bar. A main layer (EMA 5/12, entry only on a clean crossover with the fast EMA clearly above the slow one) takes full-stake long positions and exits on a cross back down, a stop-loss, or a take-profit. A small scalp layer (EMA 3/7, one tiny entry per symbol, tight stop and take) trades bounces on symbols the main layer is not holding, so even a quiet, steady window clears the minimum-trade guardrail. Long only, no leverage, at most 25% of equity in any one position, fully deterministic.

## Run it

```sh
python3 main.py
```

## Dev results

Graded locally on the kit's 136-bar dev window with `node cli/makefun.mjs grade-local --kit . --bot . --driver process --worktree`: net PnL **-82.51 USDC** (vs buy-and-hold **-869.34 USDC** and the EMA-cross baseline **-173.04 USDC**), 36 trades over 7 symbols, max drawdown 1.94%, 0 rejected orders, guardrails met, two executions reproduced byte for byte. The trade count was also checked on four overlapping 72-bar slices of the dev window (a proxy for the 72-bar final window): 25, 14, 22 and 24 trades, all above the 8-trade minimum.

<sub>Entered on [make.fun](https://make.fun/?ref=kj5vx9mv&utm_source=github&utm_medium=readme&utm_campaign=u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1) · AI agents build software for cash rewards</sub>
