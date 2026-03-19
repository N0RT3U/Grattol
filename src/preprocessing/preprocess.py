import argparse
import sys
from pathlib import Path
import pandas as pd


def memory_usage_mb(df: pd.DataFrame) -> float:
    return df.memory_usage(deep=True).sum() / 1024**2


def optimize_dtypes(df: pd.DataFrame) -> tuple[pd.DataFrame, float, float, float]:
    before_mb = memory_usage_mb(df)

    object_cols = df.select_dtypes(include=["object", "string"]).columns
    for col in object_cols:
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        unique_ratio = non_null.nunique(dropna=True) / len(non_null)
        if unique_ratio <= 0.5:
            df[col] = df[col].astype("category")

    int_cols = df.select_dtypes(include=["integer"]).columns
    for col in int_cols:
        series = df[col]
        if series.min() >= 0:
            df[col] = pd.to_numeric(series, downcast="unsigned")
        else:
            df[col] = pd.to_numeric(series, downcast="integer")

    float_cols = df.select_dtypes(include=["float"]).columns
    for col in float_cols:
        df[col] = pd.to_numeric(df[col], downcast="float")

    after_mb = memory_usage_mb(df)
    reduction_pct = 0.0 if before_mb == 0 else (before_mb - after_mb) / before_mb * 100
    return df, before_mb, after_mb, reduction_pct


TARGET_BRANDS = [
    "runail",
    "lianail",
    "irisk",
    "masura",
    "ingarden",
    "de.lux",
    "milv",
    "f.o.x",
    "cnd",
    "grattol",
    "bluesky",
    "pole",
    "jessnail",
    "haruyama",
    "rosi",
    "airnails",
    "beautix",
    "uno",
    "entity",
    "beauty-free",
    "kinetics",
    "cosmoprofi",
    "pnb",
    "domix",
    "enas",
    "oniq",
    "sophin",
    "uskusi",
    "solomeya",
    "artex",
    "orly",
    "tertio",
    "inm",
    "candy",
    "i-laq",
    "blixz",
    "lamixx",
    "opi",
    "enigma",
    "rocknailstar",
    "vl-gel",
    "pueen",
    "naomi",
    "ibd",
    "dartnails",
    "yllozure",
]

TOP_BRANDS = [
    "runail",
    "grattol",
    "irisk",
    "uno",
    "masura",
    "jessnail",
    "ingarden",
    "cnd",
    "beautix",
    "cosmoprofi",
]

MERGE_KEYS = [
    "event_time",
    "event_type",
    "product_id",
    "category_id",
    "brand",
    "price",
    "user_id",
    "user_session",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess monthly cosmetic CSV files."
    )
    parser.add_argument(
        "--input-files",
        nargs="+",
        help="Monthly CSV files (e.g., 2019-Oct.csv 2019-Nov.csv).",
    )
    parser.add_argument(
        "--input-dir",
        help="Directory containing monthly CSV files.",
    )
    parser.add_argument(
        "--pattern",
        default="*.csv",
        help="Glob pattern used with --input-dir (default: *.csv).",
    )
    parser.add_argument(
        "--cluster-file",
        help="CSV file containing cluster_tag column.",
    )
    parser.add_argument(
        "--output",
        help="Output CSV file.",
    )
    parser.add_argument(
        "--cutoff",
        default="2020-02-29 23:59:59",
        help="Cutoff datetime (local time after TZ conversion).",
    )
    parser.add_argument(
        "--tz",
        default="Etc/GMT-3",
        help="Timezone to convert event_time to.",
    )
    return parser.parse_args()


def prompt_for_path() -> str:
    return input("Monthly CSV file or folder path: ").strip()


def resolve_input_files(args: argparse.Namespace) -> list[Path]:
    files: list[Path] = []
    if args.input_files:
        files = [Path(p) for p in args.input_files]
    elif args.input_dir:
        files = sorted(Path(args.input_dir).glob(args.pattern))
    else:
        raw_path = prompt_for_path()
        if raw_path:
            path = Path(raw_path)
            if path.is_dir():
                files = sorted(path.glob(args.pattern))
            else:
                files = [path]

    files = [p for p in files if p.exists()]
    return files


def clean_event_time(series: pd.Series, tz_out: str) -> pd.Series:
    s = series.astype(str).str.replace(r"\s*UTC$", "", regex=True)
    parsed = pd.to_datetime(s, errors="coerce", utc=True)
    return parsed.dt.tz_convert(tz_out)


def ensure_columns(df: pd.DataFrame, columns: list[str], name: str) -> None:
    missing = [col for col in columns if col not in df.columns]
    if missing:
        raise ValueError(f"{name} missing columns: {missing}")


def main() -> int:
    args = parse_args()
    print(
        "전처리 할 파일 또는 폴더 경로를 입력하세요."
    )
    input_files = resolve_input_files(args)
    if not input_files:
        print("No input files found.", file=sys.stderr)
        return 1

    print(f"Loading {len(input_files)} monthly files...")
    monthly_frames = [pd.read_csv(path) for path in input_files]
    df = pd.concat(monthly_frames, ignore_index=True)

    if "price" in df.columns:
        df = df[df["price"] > 0].copy()

    if "brand" in df.columns:
        df = df[df["brand"].isin(TARGET_BRANDS)].copy()

    if "category_code" in df.columns:
        df = df.drop(columns=["category_code"])

    if "event_time" in df.columns:
        df["event_time"] = clean_event_time(df["event_time"], args.tz)

    if args.cutoff and "event_time" in df.columns:
        cutoff = pd.Timestamp(args.cutoff)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize(df["event_time"].dt.tz)
        df = df[df["event_time"] <= cutoff].copy()

    if args.cluster_file:
        cluster_path = Path(args.cluster_file)
        if cluster_path.exists():
            df_cluster = pd.read_csv(cluster_path)
            ensure_columns(df, MERGE_KEYS, "Monthly data")
            ensure_columns(df_cluster, MERGE_KEYS + ["cluster_tag"], "Cluster data")
            df = df.merge(
                df_cluster[MERGE_KEYS + ["cluster_tag"]],
                on=MERGE_KEYS,
                how="left",
            )
            if "user_session" in df.columns:
                df = df.drop(columns=["user_session"])
        else:
            print(f"Cluster file not found: {cluster_path}", file=sys.stderr)

    if "brand" in df.columns:
        df = df[df["brand"].isin(TOP_BRANDS)].copy()

    if "event_time" in df.columns:
        df["event_date"] = df["event_time"].dt.date
        df["event_hour"] = df["event_time"].dt.hour
    if "event_type" in df.columns:
        df["event_type"] = df["event_type"].astype("category")
    if "brand" in df.columns:
        df["brand"] = df["brand"].astype("category")

    df, before_mb, after_mb, reduction_pct = optimize_dtypes(df)
    print(
        f"Memory optimized: {before_mb:.2f} MB -> {after_mb:.2f} MB "
        f"({reduction_pct:.1f}% reduction)"
    )

    if args.output:
        output_path = Path(args.output)
    else:
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent
        output_path = base_dir / "clear.csv"
    df.to_csv(output_path, index=False)
    print(f"Saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
