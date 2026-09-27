# mmlu-bench

ローカル LLM ユーザが、**自分の環境の LLM がどれだけ賢いか**を同じものさしで測って共有するリポジトリです。

[bench-of-us](https://github.com/moriyasujapan/bench-of-us) が「速度」を集めるのに対して、
こちらは **MMLU の 4 択正答率** を集めます。量子化やバックエンドを変えたときに
どれだけ賢さが落ちるかは、速度と違って手元では確かめにくいためです。

OpenAI 互換 API（`/v1/chat/completions`）を喋るものなら何でも測れます
— vLLM・llama.cpp・Ollama・LM Studio・SGLang・自作のアプリなど。

---

## 参加の流れ

1. このリポジトリを fork / clone する
2. `.env` を自分の環境に合わせて書く
3. ベンチを走らせる
4. `results/<ハンドル>/<タグ>/` に結果を置いて pull request を送る
5. マージされると CI が [順位表](LEADERBOARD.md) を自動更新する

---

## 使い方

### 必要なもの

- Python 3.9 以上（**標準ライブラリのみ**。`pip install` は不要）
- Node.js 20 以上（[promptfoo](https://promptfoo.dev) の実行に使う。初回に自動で入る）
- 測定したい LLM が OpenAI 互換 API で動いていること

### 1. 問題セットを作る

```bash
python3 make-dataset.py --profile core14   # 14 教科 × 20 問 = 280 問（標準）
python3 make-dataset.py --profile quick    #  3 教科 ×  5 問 =  15 問（動作確認用）
```

Hugging Face から MMLU を取ってきて CSV にします。profile と seed が同じなら
**誰の環境でも完全に同じ問題**が選ばれるので、投稿どうしを比較できます。

### 2. 設定する

```bash
cp .env.example .env
$EDITOR .env
```

最低限、この 3 つを自分の環境に合わせます。

```ini
BENCH_BASE_URL=http://localhost:8000/v1
BENCH_MODEL=my-model          # curl $BENCH_BASE_URL/models で確認できる
BENCH_DATASET=dataset/mmlu-core14-n20-seed42.csv
```

公開されるので、`BENCH_MACHINE` には**ホスト名を入れない**でください。

```ini
BENCH_MACHINE=2x RTX 3090 — vLLM 0.11 TP=2
```

### 3. 走らせる

```bash
python3 run-bench.py --limit 5    # まず 5 問で動作確認
python3 run-bench.py              # 本番
```

`runs/<タグ>/` に次が出ます。

| ファイル | 内容 |
|----------|------|
| `summary.json` | 集計結果（**投稿するのはこれ**） |
| `run-info.json` | 測定条件（**投稿するのはこれ**） |
| `summary.md` | 人が読む用の結果表 |
| `accuracy.svg` | 教科別正答率の図 |
| `wrong-answers.json` | 間違えた問題の一覧（デバッグ用） |
| `results.json` | promptfoo の生の出力（モデルの出力全文を含む。**投稿しない**） |

ブラウザで 1 問ずつ見たいときは、実行後に表示されるコマンドで promptfoo の画面を開けます。

### 4. 投稿する

```bash
mkdir -p results/<ハンドル>/<タグ>
cp runs/<タグ>/{summary.json,run-info.json,accuracy.svg} results/<ハンドル>/<タグ>/
git switch -c result/<ハンドル>-<タグ>
git add results/
git commit -m "<ハンドル>: <モデル名> の結果を追加"
```

fork に push して pull request を作ってください。CI が内容と個人情報を検証します。

---

## 推論モデルを測るときの注意

思考（reasoning）するモデルは、答えを出す前に数百〜数千トークン使います。
`BENCH_MAX_TOKENS` が小さいと**思考の途中で打ち切られて回答が空になり、正答率が 0% になります**。

手元の推論モデル（Qwen3.5 Flash Next W4A16 / vLLM）での実測（同じ 15 問）:

| `BENCH_MAX_TOKENS` | 正答率 | 打ち切り |
|--------------------|--------|----------|
| 8 | 0.0% | 15 / 15 |
| 2048 | 80.0% | 3 / 15 |
| 4096 | 93.3% | 1 / 15 |
| **8192（既定）** | **93.3%** | **0 / 15** |

### 「上げれば打ち切りが無くなる」わけではない

4096 で打ち切られた 1 問を、`max_tokens` だけ変えて単体で投げ直した結果:

| `max_tokens` | 実際に使った | 終了理由 | 所要 |
|--------------|--------------|----------|------|
| 8192 | 5,052 | `stop` | 38 秒 |
| 16384 | 15,792 | `stop` | 127 秒 |
| 32768 | 32,768 | `length`（打ち切り） | 179 秒 |

`temperature=0`・同じ `seed` でも、**上限を上げるほど思考が長くなり**、
32768 では完走しませんでした。つまり必要量は固定ではなく、上限を上げれば
解決する問題ではありません。8192 が実測でもっとも打ち切りが少なかったので既定にしています。

打ち切られた問題は**不正解として数え**、順位表にも件数を出します。上限を上げれば
消えるものではないので、少数の打ち切りは結果の一部として受け入れます
（280 問の実測で 6 問 = 2.1%）。10% を超える投稿だけ CI が弾きます。
減らしたい場合は思考を止めて測り直してください。

```ini
BENCH_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
```

### 再現性について

上の結果が示すとおり、`temperature=0` でも**出力は完全に再現しません**。
vLLM などはリクエストのバッチ構成で数値計算の順序が変わり、投機的デコードを
使えばさらにぶれます。同じ設定で測り直しても正答率は数ポイント動きます。
`summary.json` の 95% 信頼区間は問題数によるばらつきだけを表したもので、
この実行ごとのばらつきは含んでいません。**小さな差を有意だと思わないでください。**

---

## 測っているもの・測っていないもの

**測っているもの**は、MMLU 4 択問題の正答率だけです。
4 択なのでランダムに答えても 25% 前後になります。25% を有意に上回るかで見てください。

**測っていないもの**:

- **スループット**。応答時間と tok/s も記録しますが、同時実行数 1 の 1 リクエストあたりの値で、
  投稿者のハードウェアと同時の負荷に左右されます。速度の比較には
  [bench-of-us](https://github.com/moriyasujapan/bench-of-us) を使ってください。
- **日本語能力**。MMLU は英語です。
- **実務での有用性**。MMLU は多くのモデルの学習データに含まれている可能性があり
  （コンタミネーション）、高いスコアがそのまま賢さを意味するとは限りません。
  同じモデルの量子化違い・バックエンド違いを比べる用途に向いています。

---

## 仕組み

```
make-dataset.py   MMLU を取得 → dataset/*.csv（参加者全員で共有）
      ↓
run-bench.py      .env を読む → promptfooconfig.yaml を生成 → promptfoo eval
      ↓             prompt.txt でプロンプトを組み、assert-choice.js で正誤判定
summarize.py      results.json → summary.json / summary.md / accuracy.svg
      ↓
aggregate.py      results/**/summary.json → LEADERBOARD.md / site/index.html
```

`assert-choice.js` は「`正解は A` が含まれるか」のような単純な文字列一致ではなく、
最終行・明示的な回答表明・思考ブロックの除去を順に見て選択肢を取り出します。
変更したときは `node test-extract.js` でテストしてください。

問題セットは CSV に `question,A,B,C,D,correct,subject,id` の列があればよいので、
MMLU 以外（JMMLU など）を足すのも `make-dataset.py` の差し替えだけで済みます。

---

## 環境を汚さない

promptfoo は既定でホームディレクトリ（`~/.promptfoo`, `~/.npm`）に
評価 DB とキャッシュを作りますが、`run-bench.py` は書き込み先をすべて
このディレクトリ以下に閉じ込めています。テレメトリ送信も切っています。

```
mmlu-bench/
├── node_modules/    promptfoo 本体
├── .promptfoo/      評価 DB・キャッシュ
├── .npm-cache/      npm キャッシュ
└── runs/            測定結果
```

消すときはディレクトリごと消すだけで、ホームディレクトリには何も残りません。

```bash
./clean.sh          # 実行環境だけ消す（結果と問題セットは残す）
rm -rf mmlu-bench   # 全部消す
```

---

## ライセンス

MIT。MMLU データセットは [cais/mmlu](https://huggingface.co/datasets/cais/mmlu)（MIT）に従います。
