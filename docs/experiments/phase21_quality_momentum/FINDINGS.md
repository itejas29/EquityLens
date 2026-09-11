# Phase 21: do quality filters improve momentum?

Completed 2026-09-10. 16 walk-forward folds. Five arms share one universe and
one indicator cache per fold, so the comparisons between arms are like for like.

## The question

The proposal was to combine momentum with `roe` and `eps_growth`. That was
rejected before any run. Those fields are a single current snapshot from
yfinance `.info`, so using them in a 2016 fold would hand the backtest numbers
from 2026. That is lookahead, and no fold result built on it would mean anything.

What was tested instead is **price-derived quality**. Before the momentum
ranking, candidates are filtered on their own trailing volatility and/or max
drawdown, which are known point-in-time at every rebalance.

| arm | filter |
|---|---|
| `control_V1` | none (the live strategy) |
| `lowvol_50` | keep the lower-volatility half |
| `lowdd_50` | keep the shallower-drawdown half |
| `both_50` | both, each at the 50th percentile |
| `both_70` | both, each at the 70th percentile |

The pass mark was fixed in the script before the run: **an arm counts as a
finding only if it beats the control AND beats NIFTY.**

## Result

```
arm          mean ret%   vs NIFTY   vs control   beat NIFTY   beat control   worst fold%
control_V1       9.06      +2.84          -         10/16           -          -17.36
lowvol_50        7.12      +0.90      -1.94          8/16         10/16        -13.97
lowdd_50         8.34      +2.12      -0.72          8/16         10/16        -11.51
both_50          6.62      +0.40      -2.44          9/16         10/16        -11.61
both_70          3.02      -3.20      -6.04          7/16          8/16        -15.06
NIFTY50          6.22
```

**No arm beats the control on mean return. By the fixed pass mark, Phase 21
finds nothing.**

The pattern is consistent: the filters give up upside to buy downside
protection.

- Three of the four arms beat the control in 10 of 16 folds, yet all of them
  have a lower mean. They win the quiet folds and lose the big ones. In fold 5
  (NIFTY +33.44%) the control made +41.47% and `lowvol_50` +7.91%. In fold 11
  (NIFTY +15.83%) it was +58.51% against +28.01%.
- The worst fold improves from -17.36% to -11.51% (`lowdd_50`).
- Tightening the filter to the 70th percentile (`both_70`) is worse on every
  measure, so the cost of the filter rises with its strength.

Chaining the 16 consecutive six-month test windows gives `lowdd_50` +197.7%
against the control's +185.8%, because lower volatility means less drag. That
is the only measure on which any arm leads. It is reported here because it is
true, and it does not change the verdict: the pass mark was set before the run,
and moving it after seeing the numbers would defeat its purpose.

Low volatility beating on a risk-adjusted basis is a documented anomaly
(Haugen & Heins, 1972). Even if it held here, it would be a known effect, not
new IP.

## What was lost

The script wrote `results.json` before it logged the summary table, and that
write failed. See *Defect* below. Sharpe, max drawdown and trade counts per arm
were computed and then lost. Only the per-fold total returns survive, in
`run.log`, and every number above is derived from those. So the downside
argument rests on the worst-fold return alone, not on drawdown or Sharpe.
Re-running would recover those metrics, but would not change the verdict, which
depends on mean return.

## This control is not comparable to Phase 20

Phase 20's live arm and this control run the same strategy on the same fold
windows, and NIFTY matches to the cent in every fold. The strategy returns
differ by up to ±22pp per fold, in both directions (mean 9.06% here against
5.38% there).

The cause is the candidate pool. Phase 20 drew each fold's point-in-time
top 500 from a prepared 1,000-stock pool (`phase18_prepare_universe.py`). The
lab for this run held only production's 567 stocks, so its "top 500" was
almost the whole survivor set. That is a different universe, and Phase 19
already measured that membership alone moves results by up to 12pp.

**So this control beating NIFTY is not evidence that V1 has an edge.** It is
Phase 19's finding appearing again. The comparisons within Phase 21 (arm
against control) are unaffected, because every arm saw the same universe in
every fold.

## Defect found by this run

Inside the container, scripts live at `/app/scripts/`. `Path(__file__).parents[2]`
is therefore `/`, and results went to `/docs/experiments/...`. That worked only
while the container ran as root. After the image moved to a non-root user (an
earlier hardening fix in this same audit), the write raised `PermissionError`
at the end of a run lasting several hours. Twelve scripts had this pattern. They
now use `app.core.experiment_paths.experiment_dir`, which creates the directory
at startup so an unwritable destination fails before the run starts. This
script also logs its summary before writing its file.

## Raw output

`run.log` is the complete container log. There is no `results.json`, for the
reason given above.
