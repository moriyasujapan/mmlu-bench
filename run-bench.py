#!/usr/bin/env python3
"""MMLU の CSV を promptfoo 経由で LLM アプリに投げ、正答率と応答時間を測る。

  cp .env.example .env    # 環境に合わせて編集
  python3 run-bench.py

promptfoo の状態（評価 DB・キャッシュ・node_modules・npm キャッシュ）は
すべてこのディレクトリ以下に閉じ込めてある。消したいときは

  rm -rf /path/to/mmlu-bench

だけでよく、ホームディレクトリには何も残さない。
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from run_bench_env import load_env, need, parse_extra_body  # noqa: E402


def ensure_promptfoo(sandbox_env):
    """promptfoo をプロジェクト内の node_modules に用意する。"""
    bin_path = os.path.join(ROOT, "node_modules", ".bin", "promptfoo")
    if os.path.exists(bin_path):
        return bin_path
    if shutil.which("npm") is None:
        sys.exit("npm が見つかりません。Node.js 20 以上を入れてください。")
    print("promptfoo をプロジェクト内に導入します（初回のみ・数分かかります）…", flush=True)
    r = subprocess.run(
        ["npm", "install", "--prefix", ROOT, "--no-fund", "--no-audit"],
        env=sandbox_env,
    )
    if r.returncode != 0 or not os.path.exists(bin_path):
        sys.exit("promptfoo の導入に失敗しました。")
    return bin_path


def sandboxed_env(base):
    """promptfoo と npm の書き込み先をプロジェクト内に閉じ込めた環境変数を返す。"""
    e = dict(base)
    e.update({
        # promptfoo の設定・評価 DB・キャッシュ（既定は ~/.promptfoo）
        "PROMPTFOO_CONFIG_DIR": os.path.join(ROOT, ".promptfoo"),
        "PROMPTFOO_CACHE_PATH": os.path.join(ROOT, ".promptfoo", "cache"),
        # 外部送信・更新確認をしない
        "PROMPTFOO_DISABLE_TELEMETRY": "1",
        "PROMPTFOO_DISABLE_UPDATE": "1",
        "PROMPTFOO_DISABLE_SHARING": "1",
        # npm のキャッシュ（既定は ~/.npm）
        "npm_config_cache": os.path.join(ROOT, ".npm-cache"),
        "npm_config_update_notifier": "false",
    })
    return e


def write_config(path, env, dataset, tag):
    j = json.dumps  # JSON は YAML の部分集合なのでエスケープに流用する
    extra = parse_extra_body(env)
    # promptfoo の openai プロバイダは passthrough の中身をそのまま本文に載せる
    passthrough = ""
    if extra:
        passthrough = "      passthrough:\n" + "".join(
            f"        {k}: {j(v)}\n" for k, v in extra.items()
        )
    cfg = f"""# run-bench.py が .env から自動生成したファイル。直接編集しても次回上書きされる。
description: {j(f"mmlu-bench {tag}")}

providers:
  - id: {j(f"{need(env, 'BENCH_PROVIDER', 'openai:chat')}:{need(env, 'BENCH_MODEL')}")}
    label: {j(need(env, "BENCH_MODEL"))}
    config:
      apiBaseUrl: {j(need(env, "BENCH_BASE_URL"))}
      apiKey: {j(env.get("BENCH_API_KEY") or "dummy")}
      temperature: {float(env.get("BENCH_TEMPERATURE", 0))}
      max_tokens: {int(env.get("BENCH_MAX_TOKENS", 8192))}
      seed: {int(env.get("BENCH_SEED", 42))}
{passthrough}
prompts:
  - {j("file://" + os.path.join(ROOT, "prompt.txt"))}

tests: {j("file://" + dataset)}

defaultTest:
  assert:
    - type: javascript
      value: {j("file://" + os.path.join(ROOT, "assert-choice.js"))}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(cfg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"))
    ap.add_argument("--tag", default=None, help=".env の BENCH_TAG を上書きする")
    ap.add_argument("--limit", type=int, default=None, help="先頭 N 問だけ流す（動作確認用）")
    ap.add_argument("--dry-run", action="store_true", help="設定生成と疎通確認までで止める")
    args = ap.parse_args()

    env = load_env(args.env)
    senv = sandboxed_env(os.environ)

    dataset = need(env, "BENCH_DATASET")
    if not os.path.isabs(dataset):
        dataset = os.path.join(ROOT, dataset)
    if not os.path.exists(dataset):
        sys.exit(
            f"問題セットがありません: {dataset}\n"
            "  python3 make-dataset.py --profile core14\n"
            "で生成してください。"
        )

    tag = args.tag or env.get("BENCH_TAG") or "auto"
    if tag == "auto":
        tag = time.strftime("%Y-%m-%d_%H%M%S")
    run_dir = os.path.join(ROOT, "runs", tag)
    os.makedirs(run_dir, exist_ok=True)

    # --limit は元の CSV を壊さないよう run_dir 側に切り出す
    if args.limit:
        trimmed = os.path.join(run_dir, "dataset-limited.csv")
        with open(dataset, encoding="utf-8", newline="") as fi:
            r = list(csv.DictReader(fi))
        with open(trimmed, "w", encoding="utf-8", newline="") as fo:
            w = csv.DictWriter(fo, fieldnames=r[0].keys())
            w.writeheader()
            w.writerows(r[: args.limit])
        dataset = trimmed

    # 問題文には改行が入るので、行数ではなく CSV のレコード数を数える
    with open(dataset, encoding="utf-8", newline="") as f:
        n_q = sum(1 for _ in csv.reader(f)) - 1
    base_url = need(env, "BENCH_BASE_URL")
    model = need(env, "BENCH_MODEL")

    print(f"対象        : {model} @ {base_url}")
    print(f"問題セット  : {os.path.relpath(dataset, ROOT)}（{n_q} 問）")
    print(f"同時実行数  : {env.get('BENCH_CONCURRENCY', '1')}")
    print(f"出力先      : runs/{tag}/\n")

    # 疎通確認（ここで落ちれば promptfoo の導入前に気づける）
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {env.get('BENCH_API_KEY') or 'dummy'}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = [m.get("id") for m in json.loads(r.read()).get("data", [])]
        if ids and model not in ids:
            print(f"警告: {model} はサーバの一覧にありません。利用可能: {ids}\n", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"警告: {base_url}/models に接続できませんでした（{e}）。そのまま続行します。\n",
              file=sys.stderr)

    cfg_path = os.path.join(run_dir, "promptfooconfig.yaml")
    write_config(cfg_path, env, dataset, tag)

    meta = {
        "tag": tag,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": env.get("BENCH_MACHINE", ""),
        "model": model,
        "base_url": base_url,
        "provider": env.get("BENCH_PROVIDER", "openai:chat"),
        "dataset": os.path.basename(dataset),
        "n_questions": n_q,
        "temperature": float(env.get("BENCH_TEMPERATURE", 0)),
        "max_tokens": int(env.get("BENCH_MAX_TOKENS", 8192)),
        "extra_body": parse_extra_body(env),
        "seed": int(env.get("BENCH_SEED", 42)),
        "concurrency": int(env.get("BENCH_CONCURRENCY", 1)),
    }
    with open(os.path.join(run_dir, "run-info.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if args.dry_run:
        print(f"--dry-run: {cfg_path} を生成して終了しました。")
        return

    pf = ensure_promptfoo(senv)
    results = os.path.join(run_dir, "results.json")
    cmd = [
        pf, "eval",
        "--config", cfg_path,
        "--output", results,
        "--max-concurrency", str(int(env.get("BENCH_CONCURRENCY", 1))),
        "--no-cache",          # 速度も測るのでキャッシュは使わない
        "--no-progress-bar",
    ]
    print("promptfoo eval を実行します…\n", flush=True)
    r = subprocess.run(cmd, env=senv, cwd=ROOT)
    if not os.path.exists(results):
        sys.exit(f"promptfoo が結果を出力しませんでした（exit={r.returncode}）。")

    print("\n集計します…\n", flush=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "summarize.py"), run_dir], check=False)

    print(f"\n結果一式: runs/{tag}/")
    print(f"ブラウザで詳細を見る:")
    print(f"  PROMPTFOO_CONFIG_DIR={os.path.join(ROOT, '.promptfoo')} "
          f"{os.path.relpath(pf, os.getcwd())} view")


if __name__ == "__main__":
    main()
