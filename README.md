# CORENO Market

市場ブラックホール理論（Market Black Hole Theory）とCORENO市場構造状態分類器の、**凍結仕様による前向きシャドー実験**リポジトリです。

## 現在の判定

- 危機予報器としては不採用
- 自動売買には接続しない
- R4状態分類器とBH補助観測層を、過去データで再調整せず前向きに記録する
- 次の採否判断は「独立した新規危機2件」または「5年経過」の遅い方

基準日は 2026-07-31。基準状態は `WARNING`、BH領域は `OUTSIDE`、`D_EH=2.056` です。

## 再開内容

このリポジトリは空の状態から再開しました。旧実験の学習済みモデル、元データスナップショット、完全な特徴量生成コードはまだ復元されていません。そのため、現段階では次の二層を分離しています。

1. **生データ収集層**: 市場終値を前向きに蓄積する
2. **凍結スコア受入層**: 外部で計算済みのR4/BHスナップショットを検証し、追記専用台帳へ保存する

新しい係数の推定、閾値探索、過去データへの再適合は行いません。

## ローカル実行

```bash
python -m unittest discover -s tests -v
python scripts/run_shadow.py ingest \
  --snapshot incoming/baseline_2026-07-31.json \
  --ledger /tmp/shadow_ledger.csv
python scripts/run_shadow.py evaluate --ledger /tmp/shadow_ledger.csv
```

## 主要ファイル

- `config/frozen_spec_v1.json`: 採否条件と状態閾値の凍結仕様
- `docs/EXPERIMENT_PROTOCOL.md`: 前向き実験プロトコル
- `src/coreno_market/shadow.py`: 状態判定、追記専用台帳、前向き評価
- `scripts/collect_market_data.py`: 生データの前向き収集
- `.github/workflows/`: CIと日次データ収集

研究用途であり、投資助言ではありません。
