#!/usr/bin/env python3
"""results/ 以下の投稿を集約して LEADERBOARD.md と site/index.html を作る。

  python3 aggregate.py              # 集約して書き出す
  python3 aggregate.py --validate   # PR の検証だけ（書き出さない）

投稿のレイアウト:

  results/<handle>/<tag>/summary.json   （必須・summarize.py が出力したもの）
  results/<handle>/<tag>/run-info.json  （必須）
  results/<handle>/<tag>/accuracy.svg   （任意）

標準ライブラリだけで動くので、CI で pip install は不要。
"""

import argparse
import html
import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results")

REQUIRED = ["model", "dataset", "n_questions", "n_correct", "accuracy"]

# 投稿に混ざってはいけない個人情報のパターン
PII = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP アドレスらしき文字列"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b"), "MAC アドレスらしき文字列"),
    (re.compile(r"/(?:home|Users)/(?!runner\b)[A-Za-z0-9._-]+/"), "ユーザ名を含むパス"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "メールアドレス"),
]
PII_ALLOW = re.compile(r"^(?:127\.0\.0\.1|0\.0\.0\.0|localhost|\d+\.\d+\.\d+)$")


def iter_entries():
    if not os.path.isdir(RESULTS):
        return
    for handle in sorted(os.listdir(RESULTS)):
        hdir = os.path.join(RESULTS, handle)
        if not os.path.isdir(hdir) or handle.startswith("."):
            continue
        for tag in sorted(os.listdir(hdir)):
            tdir = os.path.join(hdir, tag)
            if os.path.isfile(os.path.join(tdir, "summary.json")):
                yield handle, tag, tdir


def scan_pii(obj, path=""):
    """JSON の中身を再帰的に見て PII らしき文字列を拾う。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += scan_pii(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += scan_pii(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        for rx, what in PII:
            for m in rx.findall(obj):
                if PII_ALLOW.match(m):
                    continue
                hits.append(f"{path}: {what} ({m})")
    return hits


def validate_entry(handle, tag, tdir):
    errors, warnings = [], []
    rel = os.path.relpath(tdir, ROOT)

    if not re.fullmatch(r"[A-Za-z0-9._-]+", handle):
        errors.append(f"{rel}: ハンドル名に使えない文字が含まれています")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
        errors.append(f"{rel}: タグに使えない文字が含まれています")

    try:
        with open(os.path.join(tdir, "summary.json"), encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return [f"{rel}/summary.json が読めません: {e}"], []

    for k in REQUIRED:
        if k not in s:
            errors.append(f"{rel}/summary.json: 必須項目 {k} がありません")
    if errors:
        return errors, warnings

    n, c = s["n_questions"], s["n_correct"]
    if not (isinstance(n, int) and n > 0):
        errors.append(f"{rel}: n_questions が不正です ({n})")
    elif not (isinstance(c, int) and 0 <= c <= n):
        errors.append(f"{rel}: n_correct が不正です ({c} / {n})")
    elif abs(s["accuracy"] - c / n) > 0.001:
        errors.append(f"{rel}: accuracy が n_correct/n_questions と一致しません")

    if not os.path.isfile(os.path.join(tdir, "run-info.json")):
        errors.append(f"{rel}/run-info.json がありません")

    if not str(s.get("dataset", "")).startswith("mmlu-"):
        warnings.append(f"{rel}: 既知の問題セットではないので順位表では別枠になります "
                        f"({s.get('dataset')})")
    trunc = s.get("n_truncated", 0)
    if trunc > n * 0.10:
        errors.append(f"{rel}: {trunc}/{n} 問 (10% 超) が max_tokens で打ち切られています。"
                      "設定を見直して測り直したものを投稿してください")
    elif trunc:
        warnings.append(f"{rel}: {trunc}/{n} 問が max_tokens で打ち切られ、不正解として数えられています")
    if s.get("n_unparsed", 0) > n * 0.1:
        warnings.append(f"{rel}: 抽出失敗が {s['n_unparsed']}/{n} 問あります。"
                        "BENCH_MAX_TOKENS を増やして測り直すと正答率が上がるかもしれません")

    for fn in ("summary.json", "run-info.json", "speed.json"):
        p = os.path.join(tdir, fn)
        if os.path.isfile(p):
            try:
                hits = scan_pii(json.load(open(p, encoding="utf-8")))
            except json.JSONDecodeError:
                continue
            for h in hits:
                errors.append(f"{rel}/{fn}: 個人情報の可能性 — {h}")

    return errors, warnings


def collect():
    entries = []
    for handle, tag, tdir in iter_entries():
        with open(os.path.join(tdir, "summary.json"), encoding="utf-8") as f:
            s = json.load(f)
        info = {}
        p = os.path.join(tdir, "run-info.json")
        if os.path.isfile(p):
            info = json.load(open(p, encoding="utf-8"))
        # speed.json は任意。あればストリーミング実測の TTFT / decode を使う。
        # summary.json 側の応答時間は推論モデルでは「どれだけ考えたか」に
        # 支配されるので、順位表には載せない。
        speed = {}
        sp = os.path.join(tdir, "speed.json")
        if os.path.isfile(sp):
            try:
                speed = json.load(open(sp, encoding="utf-8"))
            except json.JSONDecodeError:
                speed = {}
        entries.append({
            "handle": handle,
            "tag": tag,
            "dir": os.path.relpath(tdir, ROOT).replace(os.sep, "/"),
            "model": s.get("model") or info.get("model") or "(不明)",
            "machine": s.get("machine") or info.get("machine") or "",
            "dataset": s.get("dataset", "(不明)"),
            "n": s["n_questions"],
            "correct": s["n_correct"],
            "accuracy": s["accuracy"],
            "ci": s.get("accuracy_ci95") or [None, None],
            "unparsed": s.get("n_unparsed", 0),
            "truncated": s.get("n_truncated", 0),
            "ttft_ms": (speed.get("ttft_ms") or {}).get("median"),
            "decode_tps": (speed.get("decode_tok_s") or {}).get("median"),
            "speed_gen_tokens": speed.get("gen_tokens_requested"),
            "date": s.get("date") or info.get("date", ""),
            "has_svg": os.path.isfile(os.path.join(tdir, "accuracy.svg")),
        })
    return entries


def pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def num(x, unit=""):
    return "-" if x is None else f"{x:g}{unit}"


def render_markdown(entries):
    if not entries:
        return "# 順位表\n\nまだ投稿がありません。`results/<ハンドル>/<タグ>/` に結果を追加する PR を送ってください。\n"

    by_ds = defaultdict(list)
    for e in entries:
        by_ds[e["dataset"]].append(e)

    out = ["# 順位表", "",
           "`results/` に集まった投稿を CI が自動集計したものです。**このファイルは手で編集しないでください**"
           "（マージのたびに上書きされます）。", "",
           "正答率は問題セットが同じもの同士でしか比較できないので、問題セットごとに表を分けています。", ""]

    for ds in sorted(by_ds, key=lambda d: (-len(by_ds[d]), d)):
        rows = sorted(by_ds[ds], key=lambda e: -e["accuracy"])
        n_q = rows[0]["n"]
        out += [f"## `{ds}`（{n_q} 問 · {len(rows)} 件）", "",
                "| # | モデル | 正答率 | 95% CI | 構成 | TTFT | decode | 打切 | 投稿者 | 詳細 |",
                "|---|--------|--------|--------|------|------|--------|------|--------|------|"]
        for i, e in enumerate(rows, 1):
            ci = (f"{pct(e['ci'][0])} – {pct(e['ci'][1])}"
                  if e["ci"][0] is not None else "-")
            machine = e["machine"].replace("|", r"\|") or "-"
            model = e["model"].replace("|", r"\|")
            out.append(
                f"| {i} | `{model}` | **{pct(e['accuracy'])}** ({e['correct']}/{e['n']}) | {ci} "
                f"| {machine} | {num(e['ttft_ms'], ' ms')} | {num(e['decode_tps'], ' tok/s')} "
                f"| {e['truncated'] or '-'} | {e['handle']} | [結果]({e['dir']}/) |"
            )
        out.append("")

    out += ["---", "",
            "- 4 択なのでランダム回答でも 25% 前後になります。25% を有意に上回るかで見てください。",
            "- TTFT と decode は `measure-speed.py` による同時実行 1 での実測です"
            "（投稿に `speed.json` がある場合のみ）。バッチ時のスループットではありません。",
            "- 速度を本格的に比べるなら "
            "[bench-of-us](https://github.com/jimoto-no-llm/bench-of-us) を見てください。", ""]
    return "\n".join(out)


def render_html(entries):
    by_ds = defaultdict(list)
    for e in entries:
        by_ds[e["dataset"]].append(e)

    sections = []
    for ds in sorted(by_ds, key=lambda d: (-len(by_ds[d]), d)):
        rows = sorted(by_ds[ds], key=lambda e: -e["accuracy"])
        bars = []
        for e in rows:
            w = max(e["accuracy"] * 100, 0.5)
            bars.append(
                f'<tr><th scope="row"><code>{html.escape(e["model"])}</code>'
                f'<span class="sub">{html.escape(e["machine"] or e["handle"])}</span></th>'
                f'<td class="barcell"><div class="bar" style="width:{w:.1f}%"></div>'
                f'<span class="val">{pct(e["accuracy"])} '
                f'<small>({e["correct"]}/{e["n"]})</small></span></td>'
                f'<td class="n">{num(e["ttft_ms"], " ms")}</td>'
                f'<td class="n">{num(e["decode_tps"], " tok/s")}</td>'
                f'<td class="n">{html.escape(e["handle"])}</td></tr>'
            )
        sections.append(
            f'<section><h2><code>{html.escape(ds)}</code>'
            f'<span class="sub">{rows[0]["n"]} 問 · {len(rows)} 件</span></h2>'
            f'<table><thead><tr><th>モデル</th><th>正答率</th><th>TTFT</th>'
            f'<th>decode</th><th>投稿者</th></tr></thead>'
            f'<tbody>{"".join(bars)}</tbody></table></section>'
        )

    if not sections:
        sections = ['<section><p class="empty">まだ投稿がありません。</p></section>']

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mmlu-bench 順位表</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff; --fg: #1f2933; --sub: #69707d;
    --line: #e4e7eb; --bar: #4c7ef3; --bar-bg: #f0f2f5; --ref: #c0392b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #14161a; --fg: #e8eaed; --sub: #9aa0a6;
             --line: #2a2e35; --bar: #6f9cff; --bar-bg: #1e2127; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px 16px 64px; background: var(--bg); color: var(--fg);
         font: 15px/1.6 system-ui, "Hiragino Sans", "Noto Sans JP", sans-serif; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  .lede {{ color: var(--sub); margin: 0 0 32px; font-size: 14px; }}
  h2 {{ font-size: 16px; margin: 40px 0 12px; display: flex; align-items: baseline;
        gap: 10px; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
  .sub {{ color: var(--sub); font-size: 12px; font-weight: 400; display: block; }}
  h2 .sub {{ display: inline; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line);
            vertical-align: middle; }}
  thead th {{ font-size: 12px; color: var(--sub); font-weight: 500; white-space: nowrap; }}
  tbody th {{ font-weight: 600; font-size: 13px; }}
  tbody th code {{ font-size: 13px; }}
  .barcell {{ position: relative; min-width: 200px; width: 50%; background: var(--bar-bg);
              border-radius: 3px; background-clip: padding-box; }}
  .bar {{ position: absolute; left: 0; top: 6px; bottom: 6px; background: var(--bar);
          border-radius: 3px; }}
  .val {{ position: relative; font-variant-numeric: tabular-nums; font-size: 13px;
          mix-blend-mode: difference; color: #fff; padding-left: 8px; }}
  .n {{ font-variant-numeric: tabular-nums; color: var(--sub); font-size: 13px;
        white-space: nowrap; }}
  .empty {{ color: var(--sub); }}
  footer {{ margin-top: 48px; color: var(--sub); font-size: 13px; }}
  @media (max-width: 640px) {{
    th, td {{ padding: 8px 4px; font-size: 12px; }}
    .barcell {{ min-width: 120px; }}
  }}
</style>
</head>
<body><div class="wrap">
<h1>mmlu-bench 順位表</h1>
<p class="lede">MMLU による 4 択正答率。問題セットが同じもの同士でのみ比較できます。
4 択なのでランダム回答でも 25% 前後になります。</p>
{"".join(sections)}
<footer>TTFT と decode は同時実行 1 での実測です（投稿に speed.json がある場合のみ表示）。
バッチ時のスループットではありません。</footer>
</div></body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="検証のみ。書き出さない")
    args = ap.parse_args()

    all_err, all_warn = [], []
    for handle, tag, tdir in iter_entries():
        e, w = validate_entry(handle, tag, tdir)
        all_err += e
        all_warn += w

    for w in all_warn:
        print(f"::warning::{w}" if os.getenv("GITHUB_ACTIONS") else f"警告: {w}")
    for e in all_err:
        print(f"::error::{e}" if os.getenv("GITHUB_ACTIONS") else f"エラー: {e}", file=sys.stderr)

    if all_err:
        sys.exit(1)

    entries = collect()
    print(f"投稿 {len(entries)} 件を検証しました。")

    if args.validate:
        return

    with open(os.path.join(ROOT, "LEADERBOARD.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(entries))
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    with open(os.path.join(ROOT, "site", "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(entries))
    with open(os.path.join(ROOT, "site", "leaderboard.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print("LEADERBOARD.md / site/index.html / site/leaderboard.json を更新しました。")


if __name__ == "__main__":
    main()
