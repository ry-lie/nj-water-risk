"""
Part 1-C: Interactive web map of contaminated sites per water service area

Builds a clickable Leaflet map (via folium) with two switchable layers:
  - Number of known contaminated sites per service area
  - Density (sites per square mile), which corrects for area size

Output: docs/index.html  (GitHub Pages serves this folder)

Run from the project root, after part1b_analysis.py works:
    python scripts/part1c_webmap.py
"""

from pathlib import Path

import folium

from part1b_analysis import clean_areas, count_sites, load_layers

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

SIMPLIFY_TOLERANCE_FT = 100   # smaller file, no visible change at state scale
COUNT_BINS = [0, 48, 206, 460, 826]          # same breaks as the QGIS map
DENSITY_BINS = [0, 1, 5, 10, 20]             # sites per sq mi
COLORS = ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"]
ATTRIBUTION = "Data: NJDEP Bureau of GIS (accessed Nov 2026)"


def color_for(value, bins):
    color = COLORS[0]
    for edge, c in zip(bins, COLORS):
        if value >= edge:
            color = c
    return color


def add_layer(m, gdf, field, bins, name, show):
    folium.GeoJson(
        gdf,
        name=name,
        show=show,
        style_function=lambda f: {
            "fillColor": color_for(f["properties"][field], bins),
            "color": "#555555",
            "weight": 0.3,
            "fillOpacity": 0.75,
        },
        highlight_function=lambda f: {"weight": 2, "color": "#000000"},
        tooltip=folium.GeoJsonTooltip(
            fields=["SYS_NAME", "n_sites", "area_sqmi", "density_per_sqmi"],
            aliases=["Water system", "Contaminated sites",
                     "Area (sq mi)", "Sites per sq mi"],
            localize=True,
        ),
    ).add_to(m)


def add_legend(m):
    rows = "".join(
        f'<div><span style="background:{c};width:14px;height:14px;'
        f'display:inline-block;margin-right:6px;border:1px solid #999"></span>'
        f"{cnt} &nbsp;|&nbsp; {den}</div>"
        for c, cnt, den in zip(
            COLORS,
            ["0–47", "48–205", "206–459", "460–825", "826+"],
            ["&lt;1", "1–5", "5–10", "10–20", "20+"],
        )
    )
    html = (
        '<div style="position:fixed;bottom:24px;left:24px;z-index:9999;'
        'background:white;padding:10px 12px;border-radius:6px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.3);font:13px sans-serif">'
        "<b>Count &nbsp;|&nbsp; Sites per sq mi</b>"
        f"{rows}<div style='margin-top:6px;color:#666'>{ATTRIBUTION}</div></div>"
    )
    m.get_root().html.add_child(folium.Element(html))


def add_title(m):
    html = (
        '<div style="position:fixed;top:12px;left:60px;z-index:9999;'
        'background:white;padding:8px 14px;border-radius:6px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.3);font:15px sans-serif">'
        "<b>Known Contaminated Sites per Public Water Service Area, NJ</b><br>"
        '<span style="font-size:12px;color:#555">'
        "Hover an area for details. Switch layers at top right.</span></div>"
    )
    m.get_root().html.add_child(folium.Element(html))


def main():
    areas, sites = load_layers()
    areas = clean_areas(areas)
    result, _ = count_sites(areas, sites)

    web = result[["SYS_NAME", "n_sites", "area_sqmi",
                  "density_per_sqmi", "geometry"]].copy()
    web["area_sqmi"] = web["area_sqmi"].round(1)
    web["density_per_sqmi"] = web["density_per_sqmi"].round(1)
    web["geometry"] = web.geometry.simplify(SIMPLIFY_TOLERANCE_FT)
    web = web.to_crs(4326)

    m = folium.Map(location=[40.1, -74.6], zoom_start=8, tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
              "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Basemap © Esri",
        name="Basemap",
        control=False,
    ).add_to(m)
    add_layer(m, web, "n_sites", COUNT_BINS, "Number of sites", show=True)
    add_layer(m, web, "density_per_sqmi", DENSITY_BINS,
              "Sites per square mile", show=False)
    folium.LayerControl(collapsed=False).add_to(m)
    add_title(m)
    add_legend(m)

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "index.html"
    m.save(out)
    size_mb = out.stat().st_size / 1_000_000
    print(f"Saved web map -> {out.relative_to(ROOT)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
