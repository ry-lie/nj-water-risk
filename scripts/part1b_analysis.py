"""
Part 1-B: Contaminated sites per public water service area (New Jersey)

Reproduces the QGIS analysis in code so it can be re-run whenever NJDEP
updates the data (the Known Contaminated Sites List is updated daily).

Steps
  1. Load NJDEP service areas (polygons) and Known Contaminated Sites (points)
  2. Put both layers in the same CRS (EPSG:3424, NJ State Plane, US feet)
  3. Repair invalid polygon geometries (one feature failed in QGIS)
  4. Spatial join: which service area is each site in?
  5. Count sites per area and compute density (sites per square mile)
  6. Save results to SQLite and answer questions with SQL

Data: NJDEP Bureau of GIS. Raw data is NOT committed to this repo
(see .gitignore); download it into data/ before running.

Run from the project root:
    python scripts/part1b_analysis.py
"""

import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"
DB_PATH = OUT / "nj_water_risk.db"

CRS_NJ_FEET = 3424            # NAD83 / New Jersey (ftUS)
SQFT_PER_SQMI = 27_878_400
MIN_AREA_FOR_DENSITY = 1.0    # sq mi; tiny areas give misleading densities


def find_shapefile(folder: Path) -> Path:
    files = list(folder.glob("*.shp"))
    if not files:
        raise FileNotFoundError(f"No .shp file found in {folder}")
    return files[0]


def load_layers():
    areas = gpd.read_file(find_shapefile(DATA / "purveyor"))
    sites = gpd.read_file(find_shapefile(DATA / "kcsl"))
    print(f"Loaded {len(areas)} service areas, {len(sites)} contaminated sites")
    return areas.to_crs(CRS_NJ_FEET), sites.to_crs(CRS_NJ_FEET)


def clean_areas(areas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    invalid = ~areas.geometry.is_valid
    print(f"Invalid polygon geometries found: {invalid.sum()} -> repairing")
    areas = areas.copy()
    areas.loc[invalid, "geometry"] = areas.loc[invalid, "geometry"].make_valid()
    areas["area_id"] = range(1, len(areas) + 1)
    return areas


def count_sites(areas: gpd.GeoDataFrame, sites: gpd.GeoDataFrame):
    sites = sites.copy()
    sites["site_id"] = range(1, len(sites) + 1)

    joined = gpd.sjoin(
        sites[["site_id", "geometry"]],
        areas[["area_id", "geometry"]],
        how="left",
        predicate="within",
    )

    counts = joined.groupby("area_id").size().rename("n_sites")
    result = areas.merge(counts, on="area_id", how="left")
    result["n_sites"] = result["n_sites"].fillna(0).astype(int)
    result["area_sqmi"] = result.geometry.area / SQFT_PER_SQMI
    result["density_per_sqmi"] = result["n_sites"] / result["area_sqmi"]

    # One row per site; area_id is empty when the site is outside every
    # public water service area (those places usually rely on private wells).
    site_table = joined[["site_id", "area_id"]].drop_duplicates("site_id")
    return result, site_table


def save_to_sqlite(result: gpd.GeoDataFrame, site_table: pd.DataFrame):
    name_col = "SYS_NAME" if "SYS_NAME" in result.columns else result.columns[0]
    id_col = "PWID" if "PWID" in result.columns else None

    areas_df = pd.DataFrame({
        "area_id": result["area_id"],
        "pwid": result[id_col] if id_col else None,
        "sys_name": result[name_col],
        "area_sqmi": result["area_sqmi"].round(3),
        "n_sites": result["n_sites"],
        "density_per_sqmi": result["density_per_sqmi"].round(3),
    })

    OUT.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        areas_df.to_sql("service_areas", conn, if_exists="replace", index=False)
        site_table.to_sql("sites", conn, if_exists="replace", index=False)
    print(f"Saved SQLite database -> {DB_PATH.relative_to(ROOT)}")


QUERIES = {
    "Q1. Top 10 service areas by number of contaminated sites": """
        SELECT sys_name, n_sites, ROUND(area_sqmi, 1) AS area_sqmi
        FROM service_areas
        ORDER BY n_sites DESC
        LIMIT 10;
    """,
    "Q2. Top 10 by density (sites per sq mi), areas >= 1 sq mi": f"""
        SELECT sys_name, n_sites, ROUND(area_sqmi, 1) AS area_sqmi,
               ROUND(density_per_sqmi, 1) AS sites_per_sqmi
        FROM service_areas
        WHERE area_sqmi >= {MIN_AREA_FOR_DENSITY}
        ORDER BY density_per_sqmi DESC
        LIMIT 10;
    """,
    "Q3. Sites inside vs. outside public water service areas": """
        SELECT CASE WHEN area_id IS NULL
                    THEN 'Outside (likely private wells)'
                    ELSE 'Inside public water area' END AS location,
               COUNT(*) AS n_sites,
               ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM sites), 1) AS pct
        FROM sites
        GROUP BY location;
    """,
}


def run_queries():
    with sqlite3.connect(DB_PATH) as conn:
        for i, (title, sql) in enumerate(QUERIES.items(), start=1):
            df = pd.read_sql_query(sql, conn)
            print(f"\n{title}\n{df.to_string(index=False)}")
            df.to_csv(OUT / f"query{i}.csv", index=False)
    print(f"\nQuery results saved -> outputs/query1.csv ... query{len(QUERIES)}.csv")


def main():
    areas, sites = load_layers()
    areas = clean_areas(areas)
    result, site_table = count_sites(areas, sites)
    save_to_sqlite(result, site_table)
    run_queries()


if __name__ == "__main__":
    main()
