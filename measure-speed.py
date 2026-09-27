#!/usr/bin/env python3
"""TTFT と decode 速度をストリーミングで測る。

`run-bench.py` が記録する応答時間は、推論モデルでは「どれだけ考えたか」に
支配されてしまい、マシンの速さを表さない。こちらは生成長を固定し、
最初のトークンまでの時間（TTFT）と 1 トークンあたりの生成速度を分けて測る。

  python3 measure-speed.py                       # 既定（prompt 512 / gen 128 / 5 回）
  python3 measure-speed.py --gen-tokens 512 --runs 10
  python3 measure-speed.py --prompt-tokens 4096  # prefill の効きを見る

標準ライブラリだけで動く。結果は runs/speed-<日時>/speed.json に出る。
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from run_bench_env import load_env, parse_extra_body  # noqa: E402


def build_prompt(target_tokens):
    """おおよそ target_tokens 個になる、思考を誘発しない無害な文章を作る。"""
    # 英語の散文はおおむね 0.75 語/トークン。多めに作って報告は実測値でする。
    sentence = ("The quick brown fox jumps over the lazy dog near the quiet river "
                "while the morning light moves slowly across the open field. ")
    words_needed = int(target_tokens * 0.75)
    reps = max(1, words_needed // len(sentence.split()) + 1)
    body = (sentence * reps).strip()
    return ("Read the following text, then reply with a plain continuation of it. "
            "Do not analyse it, do not explain, just continue writing in the same style.\n\n"
            + body)


def one_request(base_url, model, api_key, prompt, gen_tokens, extra):
    """1 リクエストをストリーミングで投げ、TTFT と生成速度を返す。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": gen_tokens,
        "min_tokens": gen_tokens,   # vLLM 拡張。効かないサーバでは無視される
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if extra:
        payload.update(extra)

    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )

    t0 = time.perf_counter()
    ttft = None
    chunks = 0
    usage = None

    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                # 思考を本文と別のフィールドで返すサーバがあり、名前も揃っていない
                # （vLLM は reasoning、OpenAI 互換の実装では reasoning_content）。
                # 速度を測るうえではどれもトークンなので全部数える。
                piece = next(
                    (d[k] for k in ("content", "reasoning", "reasoning_content") if d.get(k)),
                    "",
                )
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    chunks += 1
    t_end = time.perf_counter() - t0

    if ttft is None:
        raise RuntimeError("トークンが 1 つも届きませんでした")

    completion = (usage or {}).get("completion_tokens") or chunks
    prompt_tokens = (usage or {}).get("prompt_tokens")
    decode_s = max(t_end - ttft, 1e-9)
    return {
        "ttft_ms": ttft * 1000,
        "total_ms": t_end * 1000,
        "completion_tokens": completion,
        "prompt_tokens": prompt_tokens,
        # 最初のトークンは TTFT 側に含まれるので decode からは除く
        "decode_tok_s": (completion - 1) / decode_s if completion > 1 else None,
        "prefill_tok_s": (prompt_tokens / ttft) if prompt_tokens and ttft else None,
    }


def stat(vals, f):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(f(vals), 2) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.path.join(ROOT, ".env"))
    ap.add_argument("--prompt-tokens", type=int, default=512)
    ap.add_argument("--gen-tokens", type=int, default=128)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    env = load_env(args.env)
    base_url = env["BENCH_BASE_URL"]
    model = env["BENCH_MODEL"]
    api_key = env.get("BENCH_API_KEY") or "dummy"
    extra = parse_extra_body(env)
    prompt = build_prompt(args.prompt_tokens)

    print(f"対象      : {model} @ {base_url}")
    print(f"prompt    : 約 {args.prompt_tokens} トークン")
    print(f"生成長    : {args.gen_tokens} トークン")
    print(f"回数      : warmup {args.warmup} + 本測定 {args.runs}\n")

    for i in range(args.warmup):
        print(f"warmup {i + 1}/{args.warmup} …", flush=True)
        try:
            one_request(base_url, model, api_key, prompt, args.gen_tokens, extra)
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            sys.exit(f"接続できませんでした: {e}")

    rows = []
    for i in range(args.runs):
        r = one_request(base_url, model, api_key, prompt, args.gen_tokens, extra)
        rows.append(r)
        print(f"{i + 1}/{args.runs}  TTFT {r['ttft_ms']:7.1f} ms  "
              f"decode {r['decode_tok_s'] or 0:6.1f} tok/s  "
              f"({r['completion_tokens']} トークン生成)", flush=True)

    tag = args.tag or time.strftime("speed-%Y-%m-%d_%H%M%S")
    run_dir = os.path.join(ROOT, "runs", tag)
    os.makedirs(run_dir, exist_ok=True)

    out = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": env.get("BENCH_MACHINE", ""),
        "model": model,
        "prompt_tokens": rows[0].get("prompt_tokens"),
        "gen_tokens_requested": args.gen_tokens,
        "completion_tokens_median": stat([r["completion_tokens"] for r in rows], statistics.median),
        "runs": args.runs,
        "extra_body": extra,
        "ttft_ms": {
            "median": stat([r["ttft_ms"] for r in rows], statistics.median),
            "mean": stat([r["ttft_ms"] for r in rows], statistics.mean),
            "min": stat([r["ttft_ms"] for r in rows], min),
            "max": stat([r["ttft_ms"] for r in rows], max),
        },
        "decode_tok_s": {
            "median": stat([r["decode_tok_s"] for r in rows], statistics.median),
            "mean": stat([r["decode_tok_s"] for r in rows], statistics.mean),
            "min": stat([r["decode_tok_s"] for r in rows], min),
            "max": stat([r["decode_tok_s"] for r in rows], max),
        },
        "prefill_tok_s_median": stat([r["prefill_tok_s"] for r in rows], statistics.median),
        "samples": rows,
    }
    with open(os.path.join(run_dir, "speed.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\nTTFT        中央値 {out['ttft_ms']['median']} ms "
          f"(min {out['ttft_ms']['min']} / max {out['ttft_ms']['max']})")
    print(f"decode      中央値 {out['decode_tok_s']['median']} tok/s "
          f"(min {out['decode_tok_s']['min']} / max {out['decode_tok_s']['max']})")
    print(f"prefill     中央値 {out['prefill_tok_s_median']} tok/s "
          f"（prompt {out['prompt_tokens']} トークン ÷ TTFT。KV キャッシュが効くと過大に出る）")
    print(f"\n書き出し: runs/{tag}/speed.json")
    print("投稿に含めるには、結果ディレクトリに speed.json をコピーしてください。")


if __name__ == "__main__":
    main()
