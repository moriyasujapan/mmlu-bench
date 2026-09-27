"""`.env` の読み込み。run-bench.py と measure-speed.py で共有する。"""

import json
import os
import sys


def load_env(path):
    """.env を読む（KEY=VALUE / # コメント / 前後のクォート除去のみの簡易実装）。"""
    env = {}
    if not os.path.exists(path):
        sys.exit(
            f"{path} がありません。\n"
            f"  cp .env.example {path}\n"
            "を実行して、環境に合わせて編集してください。"
        )
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                print(f"警告: {path}:{lineno} を無視します: {raw.rstrip()}", file=sys.stderr)
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            env[k.strip()] = v
    return env



def need(env, key, default=None):
    v = env.get(key, default)
    if v is None or v == "":
        sys.exit(f".env の {key} が設定されていません。")
    return v



def parse_extra_body(env):
    raw = (env.get("BENCH_EXTRA_BODY") or "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f".env の BENCH_EXTRA_BODY が JSON として読めません: {e}")
    if not isinstance(v, dict):
        sys.exit(".env の BENCH_EXTRA_BODY は JSON オブジェクトにしてください。")
    return v


