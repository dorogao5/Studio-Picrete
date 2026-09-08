import test from 'node:test';
import assert from 'node:assert/strict';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkMath from 'remark-math';
import katex from 'katex';
import readableMath, { splitImportedMath } from '../src/lib/remarkReadableMath.ts';

const parse = (source) => {
  const processor = unified().use(remarkParse).use(remarkMath).use(readableMath);
  return processor.runSync(processor.parse(source));
};

test('numbered reactions in flat text macros become real list items', () => {
  const root = parse(String.raw`Выберите: $\text{1) N}_{2}+\text{O}_{2}=\text{2NO; 2) H}_{2}+\text{Cl}_{2}=\text{2HCl.}$`);
  assert.equal(root.children[1].type, 'list');
  assert.equal(root.children[1].children.length, 2);
  assert.equal(root.children[1].start, 1);
});
test('separate numbered math lines form one readable list', () => {
  const root = parse('$\\text{1) A}=B$\n$\\text{2) C}=D$');
  assert.equal(root.children[0].type, 'list');
  assert.equal(root.children[0].children.length, 2);
});
test('fractions, decimal commas, paired delimiters and environments stay intact', () => {
  for (const source of [String.raw`\frac{a;b}{c}`, String.raw`-285{,}49`, String.raw`\begin{aligned}a&=b\\c&=d\end{aligned}`, String.raw`\left(a;b\right)`]) {
    assert.deepEqual(splitImportedMath(source).map(n=>n.value), [source]);
    assert.doesNotThrow(()=>katex.renderToString(source,{throwOnError:true}));
  }
});
test('ordinary Russian prose wraps outside math and the following subscript retains its base', () => {
  const parts = splitImportedMath(String.raw`\text{Вычислить изменение энтальпии образования H}_{2}\text{O}`);
  assert.equal(parts[0].type,'text');
  assert.equal(parts[0].value,'Вычислить изменение энтальпии образования ');
  assert.equal(parts[1].value,String.raw`\text{H}_{2}\text{O}`);
});
test('nested text stays inside its fraction', () => {
  const source = String.raw`\frac{\text{энергия образования одного моля}}{n}`;
  assert.equal(splitImportedMath(source)[0].value,source);
});
test('punctuation stays attached to inline math', () => {
  const nodes = parse('Результат $x=y$, следовательно.').children[0].children;
  assert.equal(nodes[1].value,String.raw`x=y\text{,}`);
  assert.equal(nodes[2].value,' следовательно.');
});
test('prose inside code and code fences is untouched', () => {
  const root = parse('`$x;y$`\n\n```tex\n$x;y$\n```');
  assert.equal(root.children[0].children[0].value,'$x;y$');
  assert.equal(root.children[1].value,'$x;y$');
});
test('subordinate numeric labels are not mistaken for a sequential list', () => {
  assert.equal(parse('Верно 1) $a$ и 3) $b$.').children[0].type,'paragraph');
});

test('enthalpy example keeps three comparisons and three numbered reactions', () => {
  const root = parse(String.raw`Какое соотношение $|\Delta H|=|\Delta U|, |\Delta H|>|\Delta U|, |\Delta H|<|\Delta U|$ справедливо: 1) $N_2(г)+O_2(г)=2NO(г)$; 2) $N_2(г)+3H_2(г)=2NH_3(г)$; 3) $H_2O_2(ж)=H_2O(ж)+\frac12 O_2(г)$?`);
  assert.equal(root.children[0].children.filter(n => n.type === 'inlineMath').length, 3);
  assert.equal(root.children[1].type, 'list');
  assert.equal(root.children[1].children.length, 3);
  const values = root.children[1].children.flatMap(item => item.children[0].children.filter(n => n.type === 'inlineMath').map(n => n.value));
  assert.equal(values.length, 3);
  for (const value of values) assert.doesNotThrow(() => katex.renderToString(value, { throwOnError: true, strict: 'ignore' }));
});
