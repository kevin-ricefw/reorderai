"""Pre-training EDA: correlation between daily store sales and retail forecasting features."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from v2.analytics.retail_features import (
    CANDIDATE_HEATMAP_FEATURE_COLUMNS,
    HEATMAP_FEATURE_COLUMNS,
    build_attribute_guide,
    build_sample_rows,
    build_store_daily_feature_frame,
)


def build_daily_feature_frame(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
) -> pd.DataFrame:
    """Store-level daily frame with retail lags, promotion, price, calendar features."""
    return build_store_daily_feature_frame(sales, inventory)


def correlation_matrix(
    daily: pd.DataFrame,
    columns: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Pearson correlation on the given feature set."""
    cols_map = columns or HEATMAP_FEATURE_COLUMNS
    cols = [c for c in cols_map if c in daily.columns]
    numeric = daily[cols].apply(pd.to_numeric, errors="coerce")
    varying = [c for c in cols if numeric[c].std(skipna=True) > 0]
    if not varying:
        return pd.DataFrame()
    return numeric[varying].corr(method="pearson")


def correlations_with_target(
    corr: pd.DataFrame,
    target: str = "total_units",
    columns: dict[str, str] | None = None,
) -> pd.Series:
    """Sorted correlations with daily units sold (excluding self)."""
    labels = columns or HEATMAP_FEATURE_COLUMNS
    if target not in corr.columns:
        return pd.Series(dtype=float)
    s = corr[target].drop(labels=[target], errors="ignore").sort_values(key=abs, ascending=False)
    return s.rename(index=lambda k: labels.get(k, k))


def plot_correlation_heatmap(
    corr: pd.DataFrame,
    *,
    columns: dict[str, str] | None = None,
    title: str = "Strong retail features only — correlation with daily store sales",
) -> go.Figure:
    """Interactive Plotly heatmap."""
    labels_map = columns or HEATMAP_FEATURE_COLUMNS
    labels = [labels_map.get(c, c) for c in corr.columns]
    z = corr.values
    text = [[f"{v:.2f}" if pd.notna(v) else "" for v in row] for row in z]

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=labels,
            y=labels,
            text=text,
            texttemplate="%{text}",
            colorscale="RdBu",
            zmid=0,
            zmin=-1,
            zmax=1,
            colorbar={"title": "Pearson r"},
        )
    )
    fig.update_layout(
        title=title,
        xaxis={"tickangle": -45, "side": "bottom"},
        yaxis={"autorange": "reversed"},
        height=720,
        margin={"l": 200, "r": 40, "t": 60, "b": 200},
    )
    return fig


def build_correlation_eda_bundle(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
) -> dict:
    """Full EDA payload: strong + candidate heatmaps."""
    daily = build_daily_feature_frame(sales, inventory)
    corr = correlation_matrix(daily, HEATMAP_FEATURE_COLUMNS)
    with_sales = correlations_with_target(corr, "total_units", HEATMAP_FEATURE_COLUMNS)

    cand_corr = correlation_matrix(daily, CANDIDATE_HEATMAP_FEATURE_COLUMNS)
    cand_with = correlations_with_target(cand_corr, "total_units", CANDIDATE_HEATMAP_FEATURE_COLUMNS)

    return {
        "daily": daily,
        "correlation_matrix": corr,
        "correlations_with_units": with_sales,
        "candidate_correlation_matrix": cand_corr,
        "candidate_correlations_with_units": cand_with,
        "correlations_with_revenue": pd.Series(dtype=float),
        "attribute_guide": build_attribute_guide(),
        "sample_rows": build_sample_rows(daily, n=6),
        "heatmap_figure": (
            plot_correlation_heatmap(corr) if not corr.empty else go.Figure()
        ),
        "candidate_heatmap_figure": (
            plot_correlation_heatmap(
                cand_corr,
                columns=CANDIDATE_HEATMAP_FEATURE_COLUMNS,
                title="Candidate attributes (cleaned) — weekend, lags, rolling, discount",
            )
            if not cand_corr.empty
            else go.Figure()
        ),
    }


def save_correlation_outputs(
    bundle: dict,
    out_dir: Path | str,
) -> dict[str, Path]:
    """Write CSV matrices, daily features, sample rows, guide, and HTML heatmaps."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    corr = bundle["correlation_matrix"]
    if not corr.empty:
        corr_labeled = corr.rename(index=HEATMAP_FEATURE_COLUMNS, columns=HEATMAP_FEATURE_COLUMNS)
        p_matrix = out / "feature_correlation_matrix.csv"
        corr_labeled.to_csv(p_matrix)
        paths["matrix"] = p_matrix

    cand = bundle.get("candidate_correlation_matrix", pd.DataFrame())
    if cand is not None and not cand.empty:
        cand_labeled = cand.rename(
            index=CANDIDATE_HEATMAP_FEATURE_COLUMNS,
            columns=CANDIDATE_HEATMAP_FEATURE_COLUMNS,
        )
        p_cand = out / "candidate_feature_correlation_matrix.csv"
        cand_labeled.to_csv(p_cand)
        paths["candidate_matrix"] = p_cand

    p_daily = out / "daily_feature_frame.csv"
    bundle["daily"].to_csv(p_daily, index=False)
    paths["daily"] = p_daily

    guide = bundle.get("attribute_guide", pd.DataFrame())
    if not guide.empty:
        p_guide = out / "feature_attribute_guide.csv"
        guide.to_csv(p_guide, index=False)
        paths["guide"] = p_guide

    sample = bundle.get("sample_rows", pd.DataFrame())
    if not sample.empty:
        p_sample = out / "feature_sample_6rows.csv"
        sample.to_csv(p_sample, index=False)
        paths["sample"] = p_sample

    with_units = bundle.get("correlations_with_units", pd.Series(dtype=float))
    if not with_units.empty:
        p_units = out / "correlations_with_daily_units.csv"
        with_units.rename("correlation").to_csv(p_units)
        paths["with_units"] = p_units

    cand_units = bundle.get("candidate_correlations_with_units", pd.Series(dtype=float))
    if cand_units is not None and not cand_units.empty:
        p_cu = out / "candidate_correlations_with_daily_units.csv"
        cand_units.rename("correlation").to_csv(p_cu)
        paths["candidate_with_units"] = p_cu

    if not corr.empty:
        p_html = out / "feature_correlation_heatmap.html"
        bundle["heatmap_figure"].write_html(str(p_html), include_plotlyjs="cdn")
        paths["heatmap_html"] = p_html

    cand_fig = bundle.get("candidate_heatmap_figure")
    if cand_fig is not None and cand is not None and not cand.empty:
        p_html2 = out / "candidate_feature_correlation_heatmap.html"
        cand_fig.write_html(str(p_html2), include_plotlyjs="cdn")
        paths["candidate_heatmap_html"] = p_html2

    return paths
