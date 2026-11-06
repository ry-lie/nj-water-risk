"""
Part 2: Groundwater level status at USGS monitoring wells in New Jersey

Question: Are groundwater levels below normal right now, and are the
low-level wells in places without public water (where homes rely on
private wells)?

Method
  1. Find NJ wells with daily groundwater-level data (USGS parameter 72019,
     depth to water below land surface, in feet)
  2. Download the last 10 years of daily values from the USGS Water Data API
  3. For each well, compare the latest 30-day average with the same 30-day
     window in each previous year -> percentile -> status
       (depth is "how far down the water is", so deeper = drier)
  4. Spatial join with the public water service areas from Part 1
  5. Save to SQLite + GeoJSON (for the web map) + one example hydrograph

Data: USGS Water Data for the Nation (public domain).
Run from the project root:
    pip install dataretrieval
    python scripts/part2_groundwater.py
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd
from dataretrieval import waterdata

from part1b_analysis import clean_areas, load_layers

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
DB_PATH = OUT / "nj_water_risk.db"

STATE = "New Jersey"
PARAM_DEPTH = "72019"       # depth to water level, ft below land surface
YEARS = 10
WINDOW_DAYS = 30
MIN_PAST_YEARS = 5          # need enough history to call something "normal"
MAX_STALE_DAYS = 60         # skip wells whose last reading is older than this
CHUNK = 50                  # well IDs per API request

END = date.today()
START = END - timedelta(days=365 * YEARS + WINDOW_DAYS)


# ---------------------------------------------------------------- download
def find_daily_wells() -> list[str]:
    ts, _ = waterdata.get_time_series_metadata(
        state_name=STATE, parameter_code=PARAM_DEPTH, skip_geometry=True
    )
    print(f"Time series found for parameter {PARAM_DEPTH}: {len(ts)}")
    if ts.empty:
        raise SystemExit("No time series returned. Check the state name / API status.")

    col = "computation_period_identifier"
    if col in ts.columns:
        print(f"  {col} values: {ts[col].dropna().unique().tolist()}")
        daily = ts[ts[col].astype(str).str.contains("Daily", case=False, na=False)]
        if not daily.empty:
            ts = daily
    ids = sorted(ts["monitoring_location_id"].dropna().unique())
    print(f"Wells with daily groundwater data: {len(ids)}")
    return ids


def download_daily(ids: list[str]) -> pd.DataFrame:
    frames = []
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        df, _ = waterdata.get_daily(
            monitoring_location_id=chunk,
            parameter_code=PARAM_DEPTH,
            time=[START.isoformat(), END.isoformat()],
            skip_geometry=True,
        )
        print(f"  downloaded wells {i + 1}-{i + len(chunk)}: {len(df)} rows")
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    # Keep daily means when available (statistic 00003)
    if "statistic_id" in df.columns and (df["statistic_id"] == "00003").any():
        df = df[df["statistic_id"] == "00003"]

    df = df[["monitoring_location_id", "time", "value"]].copy()
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None).dt.normalize()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return (df.dropna()
              .groupby(["monitoring_location_id", "time"], as_index=False)["value"]
              .mean())


def download_locations(ids: list[str]) -> gpd.GeoDataFrame:
    frames = []
    for i in range(0, len(ids), CHUNK):
        df, _ = waterdata.get_monitoring_locations(monitoring_location_id=ids[i:i + CHUNK])
        frames.append(df)
    locs = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=4326)
    keep = ["monitoring_location_id", "monitoring_location_name",
            "county_name", "well_constructed_depth", "geometry"]
    return locs[[c for c in keep if c in locs.columns]]


# ---------------------------------------------------------------- analysis
def status_from_percentile(p: float) -> str:
    if p >= 90:
        return "Much below normal"
    if p >= 75:
        return "Below normal"
    if p > 25:
        return "Normal"
    return "Above normal"


def well_status(series: pd.DataFrame):
    latest = series["time"].max()
    if (pd.Timestamp(END) - latest).days > MAX_STALE_DAYS:
        return None

    def window_mean(end_day):
        start_day = end_day - pd.Timedelta(days=WINDOW_DAYS - 1)
        w = series[(series["time"] >= start_day) & (series["time"] <= end_day)]
        return w["value"].mean() if len(w) >= WINDOW_DAYS * 2 // 3 else None

    recent = window_mean(latest)
    if recent is None:
        return None

    past = [window_mean(latest - pd.DateOffset(years=y)) for y in range(1, YEARS + 1)]
    past = [v for v in past if v is not None]
    if len(past) < MIN_PAST_YEARS:
        return None

    # Share of past years where the water was shallower than now
    pct = 100 * sum(v < recent for v in past) / len(past)
    return {
        "latest_date": latest.date().isoformat(),
        "recent_depth_ft": round(recent, 2),
        "past_median_ft": round(pd.Series(past).median(), 2),
        "change_ft": round(recent - pd.Series(past).median(), 2),
        "n_past_years": len(past),
        "percentile": round(pct),
        "status": status_from_percentile(pct),
    }


def compute_status(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for well_id, s in daily.groupby("monitoring_location_id"):
        result = well_status(s.sort_values("time"))
        if result:
            rows.append({"monitoring_location_id": well_id, **result})
    print(f"Wells with recent data and >= {MIN_PAST_YEARS} years of history: {len(rows)}")
    return pd.DataFrame(rows)


def add_service_area(wells: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    areas, _ = load_layers()
    areas = clean_areas(areas)
    joined = gpd.sjoin(
        wells.to_crs(areas.crs),
        areas[["SYS_NAME", "geometry"]],
        how="left",
        predicate="within",
    ).drop_duplicates("monitoring_location_id")
    joined["in_public_water_area"] = joined["SYS_NAME"].notna()
    return joined.drop(columns="index_right").to_crs(4326)


# ---------------------------------------------------------------- outputs
def save(wells: gpd.GeoDataFrame):
    OUT.mkdir(exist_ok=True)
    wells.to_file(OUT / "groundwater_wells.geojson", driver="GeoJSON")
    table = pd.DataFrame(wells.drop(columns="geometry"))
    table.to_csv(OUT / "groundwater_wells.csv", index=False)
    with sqlite3.connect(DB_PATH) as conn:
        table.to_sql("wells", conn, if_exists="replace", index=False)
    print("Saved -> outputs/groundwater_wells.geojson, .csv, and 'wells' table")


QUERIES = {
    "Q4. Groundwater status of monitoring wells": """
        SELECT status, COUNT(*) AS n_wells,
               ROUND(AVG(change_ft), 2) AS avg_change_ft
        FROM wells
        GROUP BY status
        ORDER BY AVG(percentile) DESC;
    """,
    "Q5. Status inside vs. outside public water service areas": """
        SELECT CASE WHEN in_public_water_area THEN 'Inside public water area'
                    ELSE 'Outside (private wells)' END AS location,
               COUNT(*) AS n_wells,
               SUM(status IN ('Below normal', 'Much below normal')) AS n_below_normal,
               ROUND(100.0 * SUM(status IN ('Below normal', 'Much below normal'))
                     / COUNT(*), 1) AS pct_below_normal
        FROM wells
        GROUP BY location;
    """,
}


def run_queries():
    with sqlite3.connect(DB_PATH) as conn:
        for i, (title, sql) in enumerate(QUERIES.items(), start=4):
            df = pd.read_sql_query(sql, conn)
            print(f"\n{title}\n{df.to_string(index=False)}")
            df.to_csv(OUT / f"query{i}.csv", index=False)


def plot_example(daily: pd.DataFrame, wells: gpd.GeoDataFrame):
    """Hydrograph of the driest well outside public water areas (or overall)."""
    candidates = wells[~wells["in_public_water_area"]]
    if candidates.empty:
        candidates = wells
    row = candidates.sort_values(["percentile", "change_ft"], ascending=False).iloc[0]
    s = daily[daily["monitoring_location_id"] == row["monitoring_location_id"]]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(s["time"], s["value"], linewidth=0.8, color="#2b6cb0")
    ax.axhline(row["past_median_ft"], color="#999999", linestyle="--",
               label=f"Median of past {row['n_past_years']} years (same 30 days)")
    ax.invert_yaxis()   # deeper water = lower on the chart
    name = row.get("monitoring_location_name", row["monitoring_location_id"])
    ax.set_title(f"{name}\nStatus: {row['status']} ({row['percentile']}th percentile)")
    ax.set_ylabel("Depth to water (ft below land surface)")
    ax.legend(loc="lower left")
    ax.text(1, -0.15, "Data: USGS Water Data for the Nation",
            transform=ax.transAxes, ha="right", fontsize=8, color="#666666")
    fig.tight_layout()
    path = OUT / "maps" / "groundwater_example_well.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    print(f"Saved hydrograph -> {path.relative_to(ROOT)}")


def main():
    ids = find_daily_wells()
    daily = download_daily(ids)
    status = compute_status(daily)
    if status.empty:
        raise SystemExit("No wells passed the data checks. Send this output to debug.")

    locs = download_locations(status["monitoring_location_id"].tolist())
    wells = locs.merge(status, on="monitoring_location_id")
    wells = add_service_area(wells)

    save(wells)
    run_queries()
    plot_example(daily, wells)


if __name__ == "__main__":
    main()
