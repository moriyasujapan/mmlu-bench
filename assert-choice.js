/**
 * モデルの出力から選択肢 1 文字を取り出して正誤を判定する。
 *
 * 単純な contains 判定では、表記ゆれ・推論モデルの思考テキスト・
 * <think> ブロックで簡単に落ちる。実際の出力を見ると、思考を垂れ流したあと
 * 最終行に裸の 1 文字を置くモデルが多いので、末尾から順に見ていく。
 *
 * 抽出できなかったものは reason に unparsed と記録し、summarize.py が
 * 別集計する（プロンプト不適合と実力不足を混同しないため）。
 */

/** 文字列中で最後にマッチした箇所の捕獲group 1 を返す。 */
function lastMatch(text, re) {
  const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
  let m, last = null;
  while ((m = g.exec(text)) !== null) {
    last = m[1];
    if (m.index === g.lastIndex) g.lastIndex++; // 空マッチ対策
  }
  return last;
}

function extract(raw) {
  let t = typeof raw === 'string' ? raw : JSON.stringify(raw ?? '');

  // 1. 思考ブロックを落とす（</think> 以降が最終回答）
  const close = t.lastIndexOf('</think>');
  if (close !== -1) t = t.slice(close + '</think>'.length);
  t = t.replace(/<think>[\s\S]*$/i, '').trim();
  if (!t) return null;

  // 2. 最後の非空行が裸の 1 文字ならそれが答え（"D" / "(D)" / "**D**" / "D."）
  const lines = t.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length) {
    const m = lines[lines.length - 1].match(/^\**\(?([A-D])\)?\**\s*[.):,、。]?$/i);
    if (m) return m[1].toUpperCase();
  }

  // 3. 明示的な回答表明。思考の途中にも "the answer might be A" のように
  //    現れるので、必ず最後のものを採る。
  for (const re of [
    /(?:final\s+answer|answer\s+is|answer)\s*[::]?\s*\**\(?([A-D])\)?\**(?![A-Za-z])/gi,
    /正解は\s*\**\(?([A-D])\)?/g,
  ]) {
    const hit = lastMatch(t, re);
    if (hit) return hit.toUpperCase();
  }

  // 4. 先頭の裸の 1 文字（思考せず即答するモデル）
  const head = t.match(/^\s*\**\(?([A-D])\)?\**\s*(?:[.):,、。]|\s|$)/);
  if (head) return head[1].toUpperCase();

  // 5. 最後の手段: 単独で現れる A-D のうち最後のもの
  const all = t.match(/\b([A-D])\b/g);
  if (all && all.length) return all[all.length - 1].toUpperCase();

  return null;
}

module.exports = (output, context) => {
  const expected = String((context.vars && context.vars.correct) || '').trim().toUpperCase();
  const picked = extract(output);

  if (!picked) {
    const t = String(output ?? '').trim();
    return {
      pass: false,
      score: 0,
      reason: t
        ? `unparsed: 選択肢を抽出できませんでした (expected=${expected}) / ${JSON.stringify(t.slice(0, 120))}`
        : `unparsed: 出力が空です (expected=${expected})`,
    };
  }

  const ok = picked === expected;
  return {
    pass: ok,
    score: ok ? 1 : 0,
    reason: ok ? `correct: ${picked}` : `wrong: got=${picked} expected=${expected}`,
  };
};

module.exports.extract = extract; // test-extract.js から使う
