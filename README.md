# CORENO Market

市場ブラックホール理論（Market Black Hole Theory）とCORENO市場構造状態分類器の、**凍結仕様による前向きシャドー実験**リポジトリです。

## 現在の判定

- 既存R4モデルは危機予報器として不採用
- 自動売買には接続しない
- R4状態分類器とBH補助観測層は、過去データで再調整せず前向きに記録する
- 次のR4採否判断は「独立した新規危機2件」または「5年経過」の遅い方

基準日は 2026-07-31。基準状態は `WARNING`、BH領域は `OUTSIDE`、`D_EH=2.056` です。

## 二つの研究層

### 1. 凍結シャドー層

旧R4/BH仕様を変更せず、外部で計算済みのスナップショットを追記専用台帳へ保存します。旧学習済みモデルと完全な特徴量生成コードはまだ復元中です。

### 2. MBH-X1探索層

凍結シャドー層を壊さない独立実験です。1990年以降の市場終値を自動収集し、開発期間だけで候補を選択します。2020年以降は固定ホールドアウトとして扱い、結果を見た後の重み・窓・閾値変更は禁止します。

探索対象は以下です。

- 実現ボラティリティ窓
- 市場同期性の相関窓
- 動的リスク閾値の分位
- 警報の持続日数
- ドローダウン、実現ボラ、VIX、市場同期性、下落幅の重み

確認済み最適値を名乗るには、固定ホールドアウトで次をすべて通過する必要があります。

- 危機イベント2件以上
- イベント再現率 50%以上
- 誤警報 年1回以下
- Circular-shift検定 `p < 0.05`
- ROC-AUCがVIX以上

一項目でも失敗した場合、出力は `NO_CONFIRMED_OPTIMUM` となり、最高点を「最適値」とは呼びません。

## 自動実験

`Collect and run MBH-X1 experiment` が平日に次を実行します。

1. 初回は1990年まで遡って8市場を取得
2. 以後は直近データを追記
3. 単体テストを実行
4. 事前登録済み432候補を開発期間で比較
5. 固定ホールドアウトを評価
6. 日本語PDFレポートを生成
7. 結果JSON、時系列CSV、PDFをGitHub Actions artifactへ保存
8. 確認ゲートをすべて通過した場合だけ `reports/confirmed/` に最適値レポートを保存

最新の暫定レポートは `reports/latest/CORENO_market_experiment_report.pdf`、確認済みレポートは `reports/confirmed/` に置かれます。

## ローカル実行

```bash
python -m pip install -r requirements-experiment.txt
python -m unittest discover -s tests -v
python scripts/run_shadow.py ingest \
  --snapshot incoming/baseline_2026-07-31.json \
  --ledger /tmp/shadow_ledger.csv
python scripts/run_shadow.py evaluate --ledger /tmp/shadow_ledger.csv
python scripts/run_optimization.py
python scripts/generate_pdf_report.py
```

## 主要ファイル

- `config/frozen_spec_v1.json`: R4/BH前向き観測の凍結仕様
- `config/optimization_spec_v1.json`: MBH-X1探索範囲と確認ゲート
- `docs/EXPERIMENT_PROTOCOL.md`: 前向き実験プロトコル
- `src/coreno_market/shadow.py`: 状態判定、追記専用台帳、前向き評価
- `src/coreno_market/optimizer.py`: 特徴量、探索、ホールドアウト、反証検定
- `scripts/collect_market_data.py`: 市場終値の初期取得と日次追記
- `scripts/run_optimization.py`: 結果JSON・CSV生成
- `scripts/generate_pdf_report.py`: 日本語PDFレポート生成

研究用途であり、投資助言・売買推奨ではありません。
