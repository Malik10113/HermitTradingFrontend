---
title: Nautilus 2.0 rc9 backtest mechanics for binary options
author: Malik10113
date: 2026-11-18
branch: master
id: l-af9b8ae3
scope: repo
status: active
src: agent
touches:
  - docs/IMPLEMENTATION.md
---

## Discovered
- BinaryOption matching in rc9 works, but the matching engine silently drops every bar/quote whose size precision differs from instrument.size_precision. Build quantities with instrument.make_qty(); Quantity.from_int on a size_precision=2 instrument produces precision 0 and the strategy sees bars while no order ever fills.
- Bar execution requires external time bars with ts_init = bar close (open + interval). ts_init = ts_event+1ms is dropped.
- A market order submitted from on_bar fills at that bar's close, not the next open. To get next-bar-open, emit a zero-spread QuoteTick at each bar open (ts_event=ts_init=open) and submit the queued intent from on_quote for the following bar; add the quote stream before the bar stream so it replays first at the shared close timestamp.
- Custom FillModel.get_orderbook_for_fill_simulation works for market orders: return an OrderBook with a synthetic bid/ask at the profiled price. An exception in the callback aborts the fill silently (log only); do not touch order.order_side(), use order.side.
- BacktestEngine.add_venue requires an explicit fee_model; a zero-fee FeeModel subclass is accepted.
## Why
These four behaviours are undocumented traps that cost a full debugging pass; they determine the engine adapter and the execution-profile implementation.
