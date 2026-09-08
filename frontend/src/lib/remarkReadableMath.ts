import type { Root, PhrasingContent, Paragraph, RootContent } from 'mdast';
import type { InlineMath } from 'mdast-util-math';

// Prose embedded in a top-level \text{} must participate in normal text
// wrapping. Nested labels (fractions, indices, arrays) remain mathematical.
export function splitImportedMath(value: string): PhrasingContent[] {
  if (/\\(?:begin|left|right)\b/.test(value)) return [math(value)];
  const result: PhrasingContent[] = [];
  let offset = 0;
  let pending = '';
  for (const match of value.matchAll(/\\text\{([^{}\\]*)\}/g)) {
    const before = value.slice(0, match.index);
    if ((before.match(/\{/g)?.length ?? 0) !== (before.match(/\}/g)?.length ?? 0)) continue;
    let prose = match[1];
    if ((prose.match(/[А-Яа-яЁё]{2,}/g)?.length ?? 0) < 3) continue;
    let tail = prose.match(/[A-Za-z][A-Za-z0-9]*\s*$/)?.[0] ?? '';
    if (/^\s*[_^]/.test(value.slice(match.index! + match[0].length))) {
      tail = prose.match(/[^\s]+\s*$/)?.[0] ?? '';
      if (!tail) continue;
    }
    if (tail) prose = prose.slice(0, -tail.length);
    pending += value.slice(offset, match.index);
    if (pending.trim()) result.push(...splitMathClauses(pending.trim()));
    result.push({ type: 'text', value: prose });
    pending = tail ? `\\text{${tail}}` : '';
    offset = match.index! + match[0].length;
  }
  pending += value.slice(offset);
  if (pending.trim()) result.push(...splitMathClauses(pending.trim()));
  return result;
}

// Imported documents sometimes place multiple numbered reactions inside one
// \text{} run. Move only punctuation outside these flat, top-level text runs;
// never rewrite fractions, subscripts, environments or chemical expressions.
function splitMathClauses(value: string): PhrasingContent[] {
  if (/\\(?:begin|left|right)\b/.test(value)) return [math(value)];
  let depth = 0;
  const expanded = value.replace(/\\text\{([^{}]*)\}|[{}]/g, (token, text: string | undefined) => {
    if (text !== undefined) {
      if (depth !== 0) return token;
      return text.split(/([;:]|,\s+(?!\d)|(?:^|(?<=[;:]))\s*\d+\)\s*)/).filter(Boolean)
        .map(part => /^(?:[;:]|,\s+)$/.test(part) || /^\s*\d+\)\s*$/.test(part) ? part : `\\text{${part}}`).join('');
    }
    depth += token === '{' ? 1 : -1;
    return token;
  });
  // Split clause punctuation only at brace depth zero. Delimiters in a matrix or an
  // explicitly paired expression are intentionally left untouched above.
  const parts: string[] = [];
  const separators: string[] = [];
  depth = 0;
  let start = 0;
  for (let i = 0; i < expanded.length; i++) {
    if (expanded[i] === '\\') { i++; continue; }
    if (expanded[i] === '{') depth++;
    if (expanded[i] === '}') depth--;
    if (depth === 0 && (/[;:]/.test(expanded[i]) || (expanded[i] === ',' && /\s/.test(expanded[i + 1] ?? '')))) {
      parts.push(expanded.slice(start, i)); separators.push(expanded[i]); start = i + 1;
    }
  }
  parts.push(expanded.slice(start));
  if (parts.length === 1 && !/^\s*\d+\)/.test(expanded)) return [math(value)];
  const result: PhrasingContent[] = [];
  parts.forEach((part, index) => {
    const label = part.match(/^\s*(\d+)\)\s*/);
    if (index) result.push(label ? { type: 'break' } : { type: 'text', value: `${separators[index - 1]} ` });
    if (label) result.push({ type: 'text', value: `${label[1]}) ` });
    const expression = (label ? part.slice(label[0].length) : part).trim();
    if (expression) result.push(math(expression));
  });
  return result;
}

function math(value: string): InlineMath {
  return { type: 'inlineMath', value, data: { hName: 'code', hProperties: { className: ['language-math', 'math-inline'] }, hChildren: [{ type: 'text', value }] } };
}

/** Presentation-only normalization: source content and saved solutions stay intact. */
export default function remarkReadableMath() {
  return (tree: Root) => {
    const walk = (node: { children?: unknown[] }) => {
      if (!node.children) return;
      node.children = node.children.flatMap(child => {
        const item = child as { type: string; value?: string; children?: unknown[] };
        if (item.type === 'inlineMath') return splitImportedMath(item.value ?? '');
        walk(item);
        if (item.type === 'paragraph') return numberedParagraph(item as Paragraph);
        return [child];
      });
      // Keep sentence punctuation attached to the preceding atomic formula.
      for (let i = 0; i < node.children.length - 1; i++) {
        const current = node.children[i] as PhrasingContent;
        const next = node.children[i + 1] as PhrasingContent;
        if (current.type !== 'inlineMath' || next.type !== 'text') continue;
        const punctuation = next.value.match(/^[,.;:!?]+/)?.[0];
        if (!punctuation) continue;
        node.children[i] = math(`${current.value}\\text{${punctuation}}`);
        next.value = next.value.slice(punctuation.length);
      }
    };
    walk(tree);
  };
}

// Numbered reactions are actual list items, so labels stay aligned even when
// an equation or its explanation takes more than one line on a phone.
function numberedParagraph(paragraph: Paragraph): RootContent[] {
  type Piece = PhrasingContent | { type: 'label'; number: number };
  const pieces: Piece[] = paragraph.children.flatMap((child): Piece[] => {
    if (child.type !== 'text') return [child];
    const output: Piece[] = [];
    let start = 0;
    for (const match of child.value.matchAll(/(?:^|\s)(\d+)\)\s*/g)) {
      if (match.index! > start) output.push({ type: 'text', value: child.value.slice(start, match.index) });
      output.push({ type: 'label', number: Number(match[1]) });
      start = match.index! + match[0].length;
    }
    if (start < child.value.length) output.push({ type: 'text', value: child.value.slice(start) });
    return output;
  });
  const labels = pieces.filter((piece): piece is { type: 'label'; number: number } => piece.type === 'label');
  if (labels.length < 2 || labels.some((label, i) => label.number !== labels[0].number + i)) return [paragraph];
  const groups: PhrasingContent[][] = [[]];
  pieces.forEach(piece => {
    if (piece.type === 'label') groups.push([]);
    else groups[groups.length - 1].push(piece);
  });
  const clean = (group: PhrasingContent[]) => {
    while (group[0]?.type === 'break') group.shift();
    while (group[group.length - 1]?.type === 'break') group.pop();
    return group;
  };
  const intro = clean(groups.shift()!);
  return [
    ...(intro.length ? [{ type: 'paragraph' as const, children: intro }] : []),
    { type: 'list', ordered: true, start: labels[0].number, spread: false,
      children: groups.map(group => ({ type: 'listItem', spread: false, children: [{ type: 'paragraph', children: clean(group) }] })) },
  ];
}
