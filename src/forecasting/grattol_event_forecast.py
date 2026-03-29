from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from statsmodels.tsa.holtwinters import ExponentialSmoothing

matplotlib.use("Agg")
import matplotlib.pyplot as plt


@dataclass(frozen=True)
class CandidateConfig:
    name: str
    window_days: int
    cap_quantile: float | None
    trend: str | None
    damped_trend: bool
    seasonal: str
    seasonal_periods: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tune a robust ETS forecaster for daily Grattol event counts and export "
            "holdout comparisons plus a 30-day forecast."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the source CSV (for example: nail_market_v2.csv).",
    )
    parser.add_argument(
        "--brand",
        default="grattol",
        help="Brand to forecast. Default: grattol",
    )
    parser.add_argument(
        "--holdout-days",
        type=int,
        default=30,
        help="Number of trailing days reserved for evaluation. Default: 30",
    )
    parser.add_argument(
        "--forecast-days",
        type=int,
        default=30,
        help="Future horizon after the final date. Default: 30",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path("docs") / "reports" / "grattol_forecast"),
        help="Directory where comparison and forecast files are written.",
    )
    return parser.parse_args()


def load_daily_brand_events(
    csv_path: Path,
    brand: str,
    chunksize: int = 200_000,
) -> pd.DataFrame:
    brand = brand.lower()
    parts: list[pd.DataFrame] = []
    usecols = lambda col: col in {"event_date", "event_time", "brand"}

    for chunk in pd.read_csv(csv_path, usecols=usecols, chunksize=chunksize):
        chunk["brand"] = chunk["brand"].astype(str).str.lower()
        chunk = chunk[chunk["brand"] == brand].copy()
        if chunk.empty:
            continue

        if "event_date" in chunk.columns:
            chunk["event_date"] = pd.to_datetime(chunk["event_date"], errors="coerce")
        else:
            chunk["event_date"] = pd.to_datetime(
                chunk["event_time"], errors="coerce", utc=True
            ).dt.tz_convert("Europe/Moscow")
            chunk["event_date"] = chunk["event_date"].dt.normalize()

        parts.append(chunk[["event_date"]].dropna())

    if not parts:
        raise ValueError(f"No rows found for brand={brand!r} in {csv_path}")

    merged = pd.concat(parts, ignore_index=True)
    daily = (
        merged.groupby("event_date")
        .size()
        .rename("event_count")
        .to_frame()
        .sort_index()
        .asfreq("D", fill_value=0)
        .rename_axis("event_date")
        .reset_index()
    )
    daily["event_count"] = daily["event_count"].astype(float)
    return daily


def evaluate_predictions(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    y_pred_arr = np.clip(y_pred_arr, 0, None)
    return {
        "r2": float(r2_score(y_true_arr, y_pred_arr)),
        "r2_log": float(r2_score(np.log1p(y_true_arr), np.log1p(y_pred_arr))),
        "mae": float(mean_absolute_error(y_true_arr, y_pred_arr)),
        "rmse": float(np.sqrt(mean_squared_error(y_true_arr, y_pred_arr))),
        "mape": float(
            np.mean(np.abs((y_true_arr - y_pred_arr) / np.clip(y_true_arr, 1, None)))
            * 100
        ),
    }


def make_candidates(available_days: int) -> list[CandidateConfig]:
    full_window = available_days
    base = [
        CandidateConfig(
            name="hw_full_add_add",
            window_days=full_window,
            cap_quantile=None,
            trend="add",
            damped_trend=False,
            seasonal="add",
            seasonal_periods=7,
        ),
        CandidateConfig(
            name="hw_full_add_damped",
            window_days=full_window,
            cap_quantile=None,
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=7,
        ),
        CandidateConfig(
            name="hw_full_q90_add_damped",
            window_days=full_window,
            cap_quantile=0.90,
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=7,
        ),
    ]

    tuned: list[CandidateConfig] = []
    for window_days in (49, 55, 56, 57, 63):
        for cap_quantile in (None, 0.98, 0.985, 0.99):
            for damped_trend in (False, True):
                name = (
                    f"hw_recent_w{window_days}_"
                    f"q{str(cap_quantile).replace('.', '') if cap_quantile else 'none'}_"
                    f"{'damped' if damped_trend else 'plain'}"
                )
                tuned.append(
                    CandidateConfig(
                        name=name,
                        window_days=window_days,
                        cap_quantile=cap_quantile,
                        trend="add",
                        damped_trend=damped_trend,
                        seasonal="add",
                        seasonal_periods=7,
                    )
                )

    seen: set[tuple] = set()
    unique_candidates: list[CandidateConfig] = []
    for candidate in [*base, *tuned]:
        key = (
            candidate.window_days,
            candidate.cap_quantile,
            candidate.trend,
            candidate.damped_trend,
            candidate.seasonal,
            candidate.seasonal_periods,
        )
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def prepare_training_slice(
    history: pd.DataFrame,
    config: CandidateConfig,
) -> tuple[pd.Series, float | None]:
    sliced = history.tail(config.window_days).copy()
    y = sliced["event_count"].astype(float)
    cap_value = None
    if config.cap_quantile is not None:
        cap_value = float(y.quantile(config.cap_quantile))
        y = y.clip(upper=cap_value)
    return y, cap_value


def forecast_ets(
    history: pd.DataFrame,
    config: CandidateConfig,
    horizon: int,
) -> tuple[np.ndarray, float | None]:
    y, cap_value = prepare_training_slice(history, config)
    model = ExponentialSmoothing(
        y,
        trend=config.trend,
        damped_trend=config.damped_trend,
        seasonal=config.seasonal,
        seasonal_periods=config.seasonal_periods,
        initialization_method="estimated",
    )
    fit = model.fit(optimized=True, use_brute=True)
    forecast = np.clip(np.asarray(fit.forecast(horizon), dtype=float), 0, None)
    return forecast, cap_value


def compare_candidates(
    daily: pd.DataFrame,
    holdout_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(daily) <= holdout_days + 35:
        raise ValueError(
            "The series is too short for robust tuning. "
            "At least about 65 daily points are recommended."
        )

    train = daily.iloc[:-holdout_days].copy().reset_index(drop=True)
    test = daily.iloc[-holdout_days:].copy().reset_index(drop=True)

    rows: list[dict[str, object]] = []
    holdout_predictions: list[pd.DataFrame] = []

    for config in make_candidates(len(train)):
        try:
            pred, cap_value = forecast_ets(train, config, holdout_days)
            metrics = evaluate_predictions(test["event_count"], pred)
            selection_score = 0.6 * metrics["r2_log"] + 0.4 * metrics["r2"]

            rows.append(
                {
                    "model": config.name,
                    "window_days": config.window_days,
                    "cap_quantile": config.cap_quantile,
                    "cap_value": cap_value,
                    "trend": config.trend,
                    "damped_trend": config.damped_trend,
                    "seasonal": config.seasonal,
                    "seasonal_periods": config.seasonal_periods,
                    "selection_score": selection_score,
                    **metrics,
                }
            )

            holdout_predictions.append(
                pd.DataFrame(
                    {
                        "model": config.name,
                        "event_date": test["event_date"].values,
                        "actual_event_count": test["event_count"].values,
                        "predicted_event_count": pred,
                    }
                )
            )
        except Exception as exc:  # pragma: no cover - diagnostic path
            rows.append(
                {
                    "model": config.name,
                    "window_days": config.window_days,
                    "cap_quantile": config.cap_quantile,
                    "cap_value": np.nan,
                    "trend": config.trend,
                    "damped_trend": config.damped_trend,
                    "seasonal": config.seasonal,
                    "seasonal_periods": config.seasonal_periods,
                    "selection_score": np.nan,
                    "r2": np.nan,
                    "r2_log": np.nan,
                    "mae": np.nan,
                    "rmse": np.nan,
                    "mape": np.nan,
                    "error": str(exc),
                }
            )

    comparison = pd.DataFrame(rows)
    comparison = comparison.sort_values(
        ["selection_score", "rmse"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)
    holdout_df = pd.concat(holdout_predictions, ignore_index=True)
    return comparison, holdout_df


def fit_best_and_forecast(
    daily: pd.DataFrame,
    comparison: pd.DataFrame,
    forecast_days: int,
) -> tuple[CandidateConfig, pd.DataFrame]:
    best_row = comparison.iloc[0]
    best_config = CandidateConfig(
        name=str(best_row["model"]),
        window_days=int(best_row["window_days"]),
        cap_quantile=(
            None if pd.isna(best_row["cap_quantile"]) else float(best_row["cap_quantile"])
        ),
        trend=None if pd.isna(best_row["trend"]) else str(best_row["trend"]),
        damped_trend=bool(best_row["damped_trend"]),
        seasonal=str(best_row["seasonal"]),
        seasonal_periods=int(best_row["seasonal_periods"]),
    )

    forecast_values, cap_value = forecast_ets(daily, best_config, forecast_days)
    future_dates = pd.date_range(
        daily["event_date"].max() + pd.Timedelta(days=1),
        periods=forecast_days,
        freq="D",
    )
    forecast_df = pd.DataFrame(
        {
            "event_date": future_dates,
            "forecast_event_count": forecast_values,
            "selected_model": best_config.name,
            "window_days": best_config.window_days,
            "cap_quantile": best_config.cap_quantile,
            "cap_value": cap_value,
        }
    )
    return best_config, forecast_df


def save_outputs(
    output_dir: Path,
    daily: pd.DataFrame,
    comparison: pd.DataFrame,
    holdout_df: pd.DataFrame,
    best_model_name: str,
    forecast_df: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    comparison.to_csv(output_dir / "model_comparison.csv", index=False)

    best_holdout = holdout_df[holdout_df["model"] == best_model_name].copy()
    best_holdout.to_csv(output_dir / "best_model_holdout_predictions.csv", index=False)

    forecast_df.to_csv(output_dir / "best_model_future_30d_forecast.csv", index=False)
    save_forecast_plot(
        output_path=output_dir / "grattol_event_forecast.png",
        daily=daily,
        best_holdout=best_holdout,
        forecast_df=forecast_df,
    )


def save_forecast_plot(
    output_path: Path,
    daily: pd.DataFrame,
    best_holdout: pd.DataFrame,
    forecast_df: pd.DataFrame,
) -> None:
    history = daily.copy()
    history["ma7"] = history["event_count"].rolling(7, min_periods=1).mean()

    fig, ax = plt.subplots(figsize=(16, 7))
    ax.plot(
        history["event_date"],
        history["event_count"],
        color="#9bbcff",
        linewidth=1.6,
        alpha=0.45,
        label="Daily events",
    )
    ax.plot(
        history["event_date"],
        history["ma7"],
        color="#1f5eff",
        linewidth=2.4,
        label="7-day moving average",
    )
    ax.plot(
        best_holdout["event_date"],
        best_holdout["actual_event_count"],
        color="#2e7d32",
        linewidth=2.3,
        marker="o",
        markersize=4,
        label="Holdout actual",
    )
    ax.plot(
        best_holdout["event_date"],
        best_holdout["predicted_event_count"],
        color="#d84315",
        linewidth=2.3,
        marker="o",
        markersize=4,
        label="Holdout forecast",
    )
    ax.plot(
        forecast_df["event_date"],
        forecast_df["forecast_event_count"],
        color="#6a1b9a",
        linewidth=2.5,
        marker="o",
        markersize=4,
        label="Future 30-day forecast",
    )
    ax.axvline(
        best_holdout["event_date"].min(),
        color="#616161",
        linestyle="--",
        linewidth=1.5,
        label="Train/Holdout split",
    )
    ax.axvline(
        forecast_df["event_date"].min(),
        color="#424242",
        linestyle=":",
        linewidth=1.5,
        label="Forecast start",
    )
    ax.set_title("Grattol Daily Event Forecast", fontsize=16, weight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Event count")
    ax.grid(alpha=0.2, linestyle="--")
    ax.legend(ncol=2, frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    daily = load_daily_brand_events(input_path, args.brand)
    comparison, holdout_df = compare_candidates(daily, args.holdout_days)
    best_config, forecast_df = fit_best_and_forecast(
        daily,
        comparison,
        args.forecast_days,
    )

    save_outputs(
        output_dir=output_dir,
        daily=daily,
        comparison=comparison,
        holdout_df=holdout_df,
        best_model_name=best_config.name,
        forecast_df=forecast_df,
    )

    best_row = comparison.iloc[0]
    print(
        f"Loaded {len(daily)} daily points "
        f"({daily['event_date'].min().date()} -> {daily['event_date'].max().date()})"
    )
    print(f"Best model: {best_config.name}")
    print(
        "Holdout metrics | "
        f"R2={best_row['r2']:.4f}, "
        f"R2(log1p)={best_row['r2_log']:.4f}, "
        f"RMSE={best_row['rmse']:.2f}, "
        f"MAPE={best_row['mape']:.2f}%"
    )
    print(f"Saved comparison: {output_dir / 'model_comparison.csv'}")
    print(f"Saved holdout predictions: {output_dir / 'best_model_holdout_predictions.csv'}")
    print(f"Saved future forecast: {output_dir / 'best_model_future_30d_forecast.csv'}")
    print(f"Saved forecast plot: {output_dir / 'grattol_event_forecast.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
