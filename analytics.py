from flask import Blueprint, jsonify
from flask_login import login_required
from data import load_data
import pandas as pd

analytics_bp = Blueprint('analytics', __name__)


@analytics_bp.route('/stats-json')
def stats_json():
    df = load_data()
    return jsonify({
        "total":      len(df),
        "phd":        len(df[df["Daraja"].str.upper() == "PHD"]),
        "dsc":        len(df[df["Daraja"].str.upper() == "DSC"]),
        "muassasalar": df["Muassasa"].nunique(),
        "olim":       df["Olim"].nunique()
    })


@analytics_bp.route('/analytics-data')
@login_required
def analytics_data():
    df = load_data()

    top_muassasalar = (
        df[df["Muassasa"] != ""].groupby("Muassasa").size()
        .nlargest(20).reset_index(name="count")
        .rename(columns={"Muassasa": "muassasa"}).to_dict(orient="records")
    )

    daraja_counts = (
        df[df["Daraja"] != ""].groupby("Daraja").size()
        .reset_index(name="count")
        .rename(columns={"Daraja": "daraja"}).to_dict(orient="records")
    )

    trend_data = []
    sana_series = pd.to_datetime(df["Sana"], errors="coerce").dropna()
    if len(sana_series):
        tmp = pd.DataFrame({"date": sana_series})
        tmp["period"] = tmp["date"].dt.to_period("M").astype(str)
        trend_data = (tmp.groupby("period").size()
                      .reset_index(name="count")
                      .sort_values("period")
                      .to_dict(orient="records"))

    top_ixtisosliklar = (
        df[df["Ixtisoslik"] != ""].groupby("Ixtisoslik").size()
        .nlargest(15).reset_index(name="count")
        .rename(columns={"Ixtisoslik": "ixtisoslik"}).to_dict(orient="records")
    )

    top15_unis = (
        df[df["Muassasa"] != ""].groupby("Muassasa").size()
        .nlargest(15).index.tolist()
    )
    hm_df = df[df["Muassasa"].isin(top15_unis) & (df["Daraja"] != "")]
    if len(hm_df):
        pivot = pd.crosstab(hm_df["Muassasa"], hm_df["Daraja"])
        pivot = pivot.reindex(top15_unis).fillna(0).astype(int)
        heatmap = {
            "muassasalar": pivot.index.tolist(),
            "darajalar":   pivot.columns.tolist(),
            "data":        pivot.values.tolist()
        }
    else:
        heatmap = {"muassasalar": [], "darajalar": [], "data": []}

    return jsonify({
        "top_muassasalar":  top_muassasalar,
        "daraja_ratio":     daraja_counts,
        "trend":            trend_data,
        "top_ixtisosliklar": top_ixtisosliklar,
        "heatmap":          heatmap
    })
