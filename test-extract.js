// 回答抽出のテスト:  node test-extract.js
const { extract } = require('./assert-choice.js');

const cases = [
  ['A', 'A'],
  ['  (C) ', 'C'],
  ['**B**', 'B'],
  ['D.', 'D'],
  ['Answer: C', 'C'],
  ['The answer is B.', 'B'],
  ['正解は D です', 'D'],
  ['<think>maybe A, or C</think>\n\nB', 'B'],
  // 思考の途中の候補ではなく最終行を採ること
  ['Thinking: could be A ... but actually D. Need final only D.\n\n\nD', 'D'],
  // 明示表明が複数あるときは最後を採ること
  ['The answer is A. Wait, reconsider. The answer is C.', 'C'],
  // 選択肢ラベルを本文で参照していても最終行が優先されること
  ['Option A mentions X, option B mentions Y.\nC', 'C'],
  ['', null],
  ['I cannot answer this question.', null],
];

let fail = 0;
for (const [input, want] of cases) {
  const got = extract(input);
  const ok = got === want;
  if (!ok) fail++;
  console.log(`${ok ? '✔' : '✘'} ${JSON.stringify(input).slice(0, 60)} → ${got} (期待 ${want})`);
}
console.log(fail === 0 ? '\nすべて通過' : `\n${fail} 件失敗`);
process.exit(fail === 0 ? 0 : 1);
