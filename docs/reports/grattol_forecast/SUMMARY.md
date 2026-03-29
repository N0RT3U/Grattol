# Grattol Event Forecast Summary

## What changed

The old notebook Holt-Winters setup trained on the full history and was heavily pulled by late-November spikes.  
The new pipeline uses a **robust recent-window ETS model**:

- target: daily `grattol` event count
- train window: last `56` days only
- model: additive ETS with weekly seasonality (`period=7`)
- robustness: cap the top `1.5%` of training values (`q=0.985`)
- trend: additive + damped

This configuration is implemented in:

- `src/forecasting/grattol_event_forecast.py`

## Holdout result

Last-30-day holdout (`2020-01-31` to `2020-02-29`):

| model | R2 | R2(log1p) | RMSE | MAPE |
| --- | ---: | ---: | ---: | ---: |
| robust recent ETS (`w=56`, `q=0.985`, damped) | `0.2311` | `0.2645` | `618.97` | `8.56%` |
| full-history ETS + q90 cap | `0.1299` | `0.1795` | `658.43` | `8.30%` |
| old full-history damped ETS | `-0.2295` | `-0.1833` | `782.69` | `9.79%` |
| old full-history additive ETS | `-0.6207` | `-0.5832` | `898.63` | `13.18%` |

The selected model is the best one in the comparison table by the combined selection score:

- `selection_score = 0.6 * R2(log1p) + 0.4 * R2`

## Generated artifacts

- `docs/reports/grattol_forecast/model_comparison.csv`
- `docs/reports/grattol_forecast/best_model_holdout_predictions.csv`
- `docs/reports/grattol_forecast/best_model_future_30d_forecast.csv`
- `docs/reports/grattol_forecast/grattol_event_forecast.png`
- `docs/reports/grattol_forecast/MECE_EXPLAINED_KR.md`
- `docs/reports/grattol_forecast/TROUBLESHOOTING.md`

## Re-run

```powershell
python src\forecasting\grattol_event_forecast.py `
  --input "G:\데이터분석\Proj\Grattol\코스메틱\data\cosmetic\nail_market_v2.csv"
```
