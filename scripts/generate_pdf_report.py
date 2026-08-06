#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]


def fmt(value, digits=3):
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "PASS" if value else "FAIL"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_chart(timeseries_path: Path, output_path: Path, holdout_start: str) -> None:
    frame = pd.read_csv(timeseries_path, parse_dates=["date"])
    frame = frame.loc[frame["date"] >= pd.Timestamp(holdout_start)].copy()
    fig, ax = plt.subplots(figsize=(9.0, 4.3))
    ax.plot(frame["date"], frame["risk_score"], label="MBH-X1 risk score", linewidth=1.1)
    ax.plot(frame["date"], frame["dynamic_threshold"], label="Dynamic threshold", linewidth=1.0)
    alerts = frame.loc[frame["alert"] == 1]
    if not alerts.empty:
        ax.scatter(alerts["date"], alerts["risk_score"], s=12, label="Alert")
    ax.set_title("Locked holdout: score and alert threshold")
    ax.set_ylabel("Score")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default=str(ROOT / "results/latest.json"))
    parser.add_argument("--timeseries", default=str(ROOT / "results/latest_timeseries.csv"))
    parser.add_argument("--output", default=str(ROOT / "reports/latest/CORENO_market_experiment_report.pdf"))
    args = parser.parse_args()

    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    chart_path = output.with_suffix(".png")
    build_chart(Path(args.timeseries), chart_path, result["locked_holdout"]["start"])

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("JPTitle", parent=styles["Title"], fontName="HeiseiKakuGo-W5", fontSize=18, leading=24, alignment=TA_CENTER)
    h1 = ParagraphStyle("JPH1", parent=styles["Heading1"], fontName="HeiseiKakuGo-W5", fontSize=13, leading=18, spaceBefore=8, spaceAfter=6)
    body = ParagraphStyle("JPBody", parent=styles["BodyText"], fontName="HeiseiKakuGo-W5", fontSize=9.5, leading=15, alignment=TA_LEFT)
    small = ParagraphStyle("JPSmall", parent=body, fontSize=8, leading=11)

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title="CORENO Market MBH-X1 Experiment Report")
    story = [
        Paragraph("CORENO市場ブラックホール実験レポート", title_style),
        Paragraph("MBH-X1 自動収集・固定ホールドアウト検証", h1),
        Paragraph(f"実行日時（UTC）: {result['run_at_utc']}<br/>データ期間: {result['data_start']} - {result['data_end']}<br/>仕様ID: {result['spec_id']}", body),
        Spacer(1, 5 * mm),
    ]

    status = result["status"]
    status_text = "最適値を確認" if status == "OPTIMAL_CONFIRMED" else "確認済み最適値なし"
    status_color = colors.HexColor("#DDEEDD") if status == "OPTIMAL_CONFIRMED" else colors.HexColor("#F4E4D7")
    status_table = Table([[Paragraph(f"判定: {status_text}", h1)]], colWidths=[170 * mm])
    status_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), status_color), ("BOX", (0, 0), (-1, -1), 0.8, colors.grey), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    story += [status_table, Spacer(1, 4 * mm)]

    conclusion = (
        "事前登録した確認ゲートを、未使用の2020年以降ホールドアウトですべて通過しました。この候補をMBH-X1の確認済み最適値として記録します。"
        if status == "OPTIMAL_CONFIRMED"
        else "確認ゲートの少なくとも1項目を通過していません。探索結果の最高点は候補として記録しますが、最適値とは主張しません。失敗後の閾値変更も行いません。"
    )
    story.append(Paragraph(conclusion, body))

    candidate = result["best_candidate"]
    weights = candidate["weights"]
    story += [Paragraph("1. 選択された候補", h1)]
    candidate_rows = [
        ["項目", "値"],
        ["リスク閾値分位", fmt(candidate["threshold_quantile"], 2)],
        ["ボラティリティ窓", f"{candidate['vol_window']}営業日"],
        ["相関窓", f"{candidate['corr_window']}営業日"],
        ["持続条件", f"{candidate['persistence_days']}日連続"],
        ["ドローダウン重み", fmt(weights["drawdown_stress"], 2)],
        ["実現ボラ重み", fmt(weights["vol_percentile"], 2)],
        ["VIX重み", fmt(weights["vix_percentile"], 2)],
        ["同期性重み", fmt(weights["sync_percentile"], 2)],
        ["下落幅重み", fmt(weights["breadth_pressure"], 2)],
    ]
    table = Table(candidate_rows, colWidths=[80 * mm, 80 * mm], repeatRows=1)
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF3")), ("GRID", (0, 0), (-1, -1), 0.35, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [table, Spacer(1, 4 * mm)]

    dev = result["development"]
    hold = result["locked_holdout"]
    story += [Paragraph("2. 開発期間と固定ホールドアウト", h1)]
    metrics_rows = [
        ["指標", "開発期間", "固定ホールドアウト"],
        ["危機イベント数", "fold平均", fmt(hold["events"], 0)],
        ["イベント再現率", fmt(dev["event_recall"]), fmt(hold["event_recall"])],
        ["誤警報/年", fmt(dev["false_alarms_per_year"]), fmt(hold["false_alarms_per_year"])],
        ["ROC-AUC", "fold目的関数で選択", fmt(hold["auc"])],
        ["リードタイム中央値", "-", fmt(hold["median_lead_rows"], 1)],
        ["VIX AUC", "-", fmt(result["vix_baseline"]["auc"])],
        ["VIXとの差", "-", fmt(result["auc_advantage_over_vix"])],
        ["Circular-shift p値", "-", fmt(result["circular_shift_p_value"], 4)],
    ]
    metrics = Table(metrics_rows, colWidths=[58 * mm, 50 * mm, 52 * mm], repeatRows=1)
    metrics.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"), ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF3")), ("GRID", (0, 0), (-1, -1), 0.35, colors.grey), ("ALIGN", (1, 1), (-1, -1), "RIGHT")]))
    story += [metrics, Spacer(1, 4 * mm), Image(str(chart_path), width=170 * mm, height=81 * mm), Paragraph("青線がMBH-X1リスクスコア、橙線が過去データのみで計算した動的閾値、点が警報日です。", small)]

    story += [PageBreak(), Paragraph("3. 確認ゲート", h1)]
    gate_labels = {
        "minimum_holdout_events": "ホールドアウト危機数",
        "holdout_event_recall": "イベント再現率",
        "holdout_false_alarms_per_year": "年間誤警報率",
        "circular_shift_p": "Circular-shift検定",
        "auc_advantage_over_vix": "VIXに対するAUC優位性",
    }
    gate_rows = [["ゲート", "結果"]] + [[gate_labels.get(name, name), fmt(value)] for name, value in result["confirmation_checks"].items()]
    gates = Table(gate_rows, colWidths=[125 * mm, 35 * mm], repeatRows=1)
    gates.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF3")), ("GRID", (0, 0), (-1, -1), 0.35, colors.grey), ("ALIGN", (1, 1), (1, -1), "CENTER")]))
    story += [gates, Spacer(1, 5 * mm)]

    story += [Paragraph("4. 反証可能性と制約", h1)]
    story.append(Paragraph("本実験は、開発期間で候補を選び、2020年以降を固定ホールドアウトとして評価します。ホールドアウトの結果を見た後の重み・窓・閾値の変更は禁止です。既存のR4凍結シャドー仕様は変更せず、MBH-X1は独立した探索層として扱います。自動売買には接続しません。", body))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("危機はS&P 500の252営業日ドローダウンが-12%を下回る交差で定義し、60営業日の予報窓とイベント・クールダウンを用います。Circular-shift検定は時間順序を崩した帰無分布に対してAUCを比較します。", body))

    story += [Paragraph("5. 上位候補", h1)]
    top_rows = [["順位", "閾値", "Vol", "Corr", "持続", "目的関数", "再現率", "誤警報/年"]]
    for item in result["top_candidates"][:10]:
        top_rows.append([item["rank"], fmt(item["threshold_quantile"], 2), item["vol_window"], item["corr_window"], item["persistence_days"], fmt(item["median_objective"]), fmt(item["development_event_recall"]), fmt(item["development_false_alarms_per_year"])])
    top = Table(top_rows, colWidths=[14 * mm, 20 * mm, 18 * mm, 18 * mm, 18 * mm, 27 * mm, 22 * mm, 27 * mm], repeatRows=1)
    top.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"), ("FONTSIZE", (0, 0), (-1, -1), 7.2), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF3")), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("ALIGN", (0, 1), (-1, -1), "RIGHT")]))
    story += [top, Spacer(1, 5 * mm), Paragraph("研究用途の実験報告であり、投資助言・売買推奨ではありません。", small)]

    doc.build(story)
    chart_path.unlink(missing_ok=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
