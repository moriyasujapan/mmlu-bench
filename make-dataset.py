#!/usr/bin/env python3
"""MMLU から固定の問題セットを作り、promptfoo 用の CSV に変換する。

標準ライブラリだけで動く（Hugging Face datasets-server の rows API を使う）。
同じ profile / seed を指定すれば誰の環境でも完全に同じ CSV ができるので、
生成した CSV は dataset/ に commit して参加者全員で共有する。

  python3 make-dataset.py                 # 既定 profile を生成
  python3 make-dataset.py --profile quick
  python3 make-dataset.py --list-subjects
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://datasets-server.huggingface.co/rows"
DATASET = "cais/mmlu"

# 参加者間で比較するための固定 profile。
# 一度公開した profile の中身は変えない（変えると過去の結果と比較できなくなる）。
PROFILES = {
    # STEM / 人文 / 専門をまんべんなく含む 14 教科。常用の既定。
    "core14": {
        "subjects": [
            "abstract_algebra", "anatomy", "astronomy",
            "college_computer_science", "college_mathematics", "computer_security",
            "econometrics", "electrical_engineering", "formal_logic",
            "high_school_physics", "international_law", "machine_learning",
            "philosophy", "professional_medicine",
        ],
        "n_per_subject": 20,
    },
    # 動作確認用。1〜2 分で終わる。
    "quick": {
        "subjects": ["computer_security", "high_school_physics", "philosophy"],
        "n_per_subject": 5,
    },
}


def fetch(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i == retries - 1:
                raise
            print(f"    再試行 {i + 1}/{retries - 1}: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))


def rows_url(subject, split, offset, length):
    q = urllib.parse.urlencode(
        {"dataset": DATASET, "config": subject, "split": split,
         "offset": offset, "length": length}
    )
    return f"{API}?{q}"


def fetch_subject(subject, n, seed, split="test"):
    """subject から seed で決まる n 問を取り出す。n が None なら全問。"""
    head = fetch(rows_url(subject, split, 0, 1))
    total = head["num_rows_total"]

    if n is None or n >= total:
        wanted = list(range(total))
    else:
        wanted = sorted(random.Random(f"{seed}:{subject}").sample(range(total), n))

    # rows API は 1 回あたり 100 行まで。必要な行を含む範囲だけ取る。
    out, i = [], 0
    while i < len(wanted):
        start = wanted[i]
        end = start
        j = i
        while j < len(wanted) and wanted[j] - start < 100:
            end = wanted[j]
            j += 1
        page = fetch(rows_url(subject, split, start, end - start + 1))
        by_idx = {r["row_idx"]: r["row"] for r in page["rows"]}
        for k in wanted[i:j]:
            if k not in by_idx:
                raise RuntimeError(f"{subject}: row {k} が取得できませんでした")
            out.append((k, by_idx[k]))
        i = j
    return out, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="core14", choices=sorted(PROFILES) + ["full"])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None, help="出力 CSV（既定は dataset/ 以下に自動命名）")
    ap.add_argument("--list-subjects", action="store_true", help="MMLU の全教科名を表示して終了")
    args = ap.parse_args()

    if args.list_subjects:
        info = fetch("https://huggingface.co/api/datasets/cais/mmlu/parquet")
        for s in sorted(k for k in info if k not in ("all", "auxiliary_train")):
            print(s)
        return

    if args.profile == "full":
        info = fetch("https://huggingface.co/api/datasets/cais/mmlu/parquet")
        subjects = sorted(k for k in info if k not in ("all", "auxiliary_train"))
        n_per = None
        name = f"mmlu-full-seed{args.seed}"
    else:
        p = PROFILES[args.profile]
        subjects, n_per = p["subjects"], p["n_per_subject"]
        name = f"mmlu-{args.profile}-n{n_per}-seed{args.seed}"

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dataset", name + ".csv"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    rows = []
    for idx, subject in enumerate(subjects, 1):
        print(f"[{idx}/{len(subjects)}] {subject} を取得中…", flush=True)
        got, total = fetch_subject(subject, n_per, args.seed)
        for row_idx, r in got:
            ch = r["choices"]
            if len(ch) != 4:
                print(f"    選択肢が 4 個でない行を除外: {subject}#{row_idx}", file=sys.stderr)
                continue
            rows.append({
                "id": f"{subject}#{row_idx}",
                "subject": subject,
                "question": r["question"],
                "A": ch[0], "B": ch[1], "C": ch[2], "D": ch[3],
                "correct": "ABCD"[r["answer"]],
            })
        print(f"    {len(got)} 問 / 全 {total} 問", flush=True)

    fields = ["id", "subject", "question", "A", "B", "C", "D", "correct"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\n✔ {len(rows)} 問を書き出しました: {out_path}")
    print(f"  .env の BENCH_DATASET に dataset/{os.path.basename(out_path)} を設定してください。")


if __name__ == "__main__":
    main()
