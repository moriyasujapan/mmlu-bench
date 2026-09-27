#!/usr/bin/env python3
"""promptfoo の results.json を集計して summary.json / summary.md / accuracy.svg を書く。

  python3 summarize.py runs/<tag>
"""

import json
import math
import os
import sys
from collections import defaultdict


def wilson(k, n, z=1.96):
    """Wilson の 95% 信頼区間。少数問での正答率を過信しないために出す。"""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def pct(x):
    return f"{x * 100:.1f}%"


def load_results(run_dir):
    with open(os.path.join(run_dir, "results.json"), encoding="utf-8") as f:
        data = json.load(f)
    # promptfoo の出力はバージョンで入れ子が変わるので両方見る
    r = data.get("results", data)
    if isinstance(r, dict):
        r = r.get("results", [])
    if not isinstance(r, list):
        sys.exit("results.json の形式を認識できませんでした。")
    return r


def field(item, *names, default=None):
    for n in names:
        if isinstance(item, dict) and item.get(n) is not None:
            return item[n]
    return default


def svg_bar_chart(rows, title, path):
    """教科別正答率の横棒グラフ。依存ライブラリなしで SVG を直接書く。"""
    row_h, pad_l, pad_t, w = 26, 240, 56, 760
    h = pad_t + row_h * len(rows) + 46
    fg, grid, bar, ref = "#1f2933", "#d9dee3", "#4c7ef3", "#c0392b"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="DejaVu Sans, sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="16" y="26" font-size="15" font-weight="bold" fill="{fg}">{title}</text>',
        f'<text x="16" y="44" font-size="11" fill="#69707d">'
        f'横軸 = 正答率 (%)　破線 = 4 択のランダム正答 25%</text>',
    ]
    plot_w = w - pad_l - 60
    for g in range(0, 101, 25):
        x = pad_l + plot_w * g / 100
        out.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t + row_h * len(rows)}" '
                   f'stroke="{ref if g == 25 else grid}" stroke-width="1" '
                   f'{"stroke-dasharray=\"4 3\"" if g == 25 else ""}/>')
        out.append(f'<text x="{x:.1f}" y="{pad_t + row_h * len(rows) + 16}" font-size="10" '
                   f'fill="#69707d" text-anchor="middle">{g}</text>')
    for i, (label, k, n) in enumerate(rows):
        y = pad_t + i * row_h
        acc = k / n if n else 0
        out.append(f'<text x="{pad_l - 8}" y="{y + 15}" font-size="11" fill="{fg}" '
                   f'text-anchor="end">{label}</text>')
        out.append(f'<rect x="{pad_l}" y="{y + 4}" width="{plot_w * acc:.1f}" height="15" '
                   f'fill="{bar}" rx="2"/>')
        out.append(f'<text x="{pad_l + plot_w * acc + 6:.1f}" y="{y + 16}" font-size="10" '
                   f'fill="#69707d">{pct(acc)} ({k}/{n})</text>')
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main():
    if len(sys.argv) < 2:
        sys.exit("使い方: python3 summarize.py runs/<tag>")
    run_dir = os.path.abspath(sys.argv[1])
    results = load_results(run_dir)

    info_path = os.path.join(run_dir, "run-info.json")
    info = json.load(open(info_path, encoding="utf-8")) if os.path.exists(info_path) else {}

    per_subject = defaultdict(lambda: {"n": 0, "correct": 0})
    lat, out_tok = [], []
    n_correct = n_unparsed = n_error = n_truncated = 0
    wrong = []

    for it in results:
        vars_ = (field(it, "testCase", default={}) or {}).get("vars") or field(it, "vars", default={}) or {}
        subject = vars_.get("subject", "(unknown)")
        ok = bool(field(it, "success", default=False))

        gr = field(it, "gradingResult", default={}) or {}
        reasons = [c.get("reason", "") for c in (gr.get("componentResults") or [])] or [gr.get("reason", "")]
        reason = " ".join(r for r in reasons if r)

        resp = field(it, "response", default={}) or {}
        has_output = bool(str(resp.get("output") or "").strip())

        # promptfoo は採点の不一致理由も item["error"] に入れてくるので、
        # 通信エラーの判定には使えない。出力の有無と finishReason から自分で分類する。
        #   truncated … max_tokens で思考の途中に打ち切られ、回答本文が出なかった
        #   error     … 出力がまったく返ってこなかった（接続失敗・サーバ側の失敗）
        if not has_output:
            if resp.get("finishReason") == "length":
                n_truncated += 1
            else:
                n_error += 1
        if "unparsed" in reason:
            n_unparsed += 1

        per_subject[subject]["n"] += 1
        if ok:
            per_subject[subject]["correct"] += 1
            n_correct += 1
        else:
            wrong.append({
                "id": vars_.get("id", ""),
                "subject": subject,
                "expected": vars_.get("correct", ""),
                "reason": reason[:200],
            })

        ms = field(it, "latencyMs", default=None)
        if isinstance(ms, (int, float)) and ms > 0:
            lat.append(ms)
            tu = (field(it, "response", default={}) or {}).get("tokenUsage") or {}
            comp = tu.get("completion")
            if isinstance(comp, (int, float)) and comp > 0:
                out_tok.append((comp, ms))

    n = len(results)
    if n == 0:
        sys.exit("結果が 0 件です。")
    acc = n_correct / n
    lo, hi = wilson(n_correct, n)

    lat_sorted = sorted(lat)
    def q(p):
        return lat_sorted[min(len(lat_sorted) - 1, int(len(lat_sorted) * p))] if lat_sorted else 0

    summary = {
        **{k: info.get(k) for k in ("tag", "date", "machine", "model", "dataset",
                                    "temperature", "max_tokens", "seed", "concurrency") if k in info},
        "n_questions": n,
        "n_correct": n_correct,
        "accuracy": round(acc, 4),
        "accuracy_ci95": [round(lo, 4), round(hi, 4)],
        "n_unparsed": n_unparsed,
        "n_truncated": n_truncated,
        "n_error": n_error,
        "latency_ms": {
            "mean": round(sum(lat) / len(lat), 1) if lat else None,
            "p50": round(q(0.50), 1), "p90": round(q(0.90), 1), "max": round(max(lat), 1) if lat else None,
        },
        "output_tokens_per_sec_mean": (
            round(sum(c / (m / 1000) for c, m in out_tok) / len(out_tok), 2) if out_tok else None
        ),
        "per_subject": {
            s: {"n": v["n"], "correct": v["correct"], "accuracy": round(v["correct"] / v["n"], 4)}
            for s, v in sorted(per_subject.items())
        },
    }

    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "wrong-answers.json"), "w", encoding="utf-8") as f:
        json.dump(wrong, f, ensure_ascii=False, indent=2)

    rows = [(s, v["correct"], v["n"]) for s, v in sorted(per_subject.items())]
    svg_bar_chart(rows, f'{info.get("model", "model")} — MMLU 教科別正答率',
                  os.path.join(run_dir, "accuracy.svg"))

    md = [
        f'# ベンチマーク結果 — {info.get("model", "(model)")}',
        "",
        "| 項目 | 内容 |",
        "|------|------|",
        f'| 構成 | {info.get("machine") or "(未設定)"} |',
        f'| モデル | {info.get("model", "-")} |',
        f'| 問題セット | {info.get("dataset", "-")}（{n} 問） |',
        f'| temperature / max_tokens / seed | {info.get("temperature", "-")} / '
        f'{info.get("max_tokens", "-")} / {info.get("seed", "-")} |',
        f'| 同時実行数 | {info.get("concurrency", "-")} |',
        "",
        "## 総合",
        "",
        "| 指標 | 値 |",
        "|------|-----|",
        f"| 正答率 | **{pct(acc)}** ({n_correct}/{n}) |",
        f"| 95% 信頼区間 | {pct(lo)} – {pct(hi)} |",
        f"| 抽出失敗 (unparsed) | {n_unparsed} 問 |",
        f"| 出力打ち切り (max_tokens) | {n_truncated} 問 |",
        f"| API エラー | {n_error} 問 |",
        f'| 応答時間 平均 / p50 / p90 | {summary["latency_ms"]["mean"]} / '
        f'{summary["latency_ms"]["p50"]} / {summary["latency_ms"]["p90"]} ms |',
        f'| 出力 tok/s（1 リクエスト平均） | {summary["output_tokens_per_sec_mean"] or "不明"} |',
        "",
        "![教科別正答率](accuracy.svg)",
        "",
        "## 教科別",
        "",
        "| 教科 | 正答 / 問数 | 正答率 |",
        "|------|-------------|--------|",
    ]
    for s, v in summary["per_subject"].items():
        md.append(f'| {s} | {v["correct"]} / {v["n"]} | {pct(v["accuracy"])} |')
    md += ["", "※ 4 択なのでランダム回答でも 25% 前後になる。25% を有意に上回るかで見る。", ""]

    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    if n_truncated:
        md += [
            "",
            f"> **注意**: {n_truncated} 問が `max_tokens`"
            f"（{info.get('max_tokens', '?')}）で打ち切られ、回答が空になっています。",
            "> 推論モデルでは思考が上限に達すると本文が出ません。"
            "`.env` の `BENCH_MAX_TOKENS` を増やすか、",
            "> `BENCH_EXTRA_BODY` で思考を止めて測り直してください。この結果は投稿しないでください。",
            "",
        ]

    print("\n".join(md[:26]))
    if n_truncated:
        print(f"\n⚠ {n_truncated}/{n} 問が max_tokens で打ち切られました。"
              "BENCH_MAX_TOKENS を増やして測り直してください。", file=sys.stderr)
    print(f"\n書き出し: {run_dir}/summary.{{json,md}}, accuracy.svg, wrong-answers.json")


if __name__ == "__main__":
    main()
