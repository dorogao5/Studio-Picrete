import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mathRanges } from '../src/lib/mathRanges.ts';
test('all delimiters, multiline and exact source positions', () => {
 const source = String.raw`Text $a=-2$ and \(b^2\).
$$
x=y
$$
\[z=3\]`;
 const result = mathRanges(source);
 assert.equal(result.length, 4);
 assert.deepEqual(result.map(r => r.display), [false, false, true, true]);
 assert.equal(source.slice(result[0].from, result[0].to), '$a=-2$');
});
test('code and escaped dollars stay literal', () => {
 assert.equal(mathRanges('`$x$`\n```\n$y$\n```\n\\$5 and $z$').length, 1);
});
