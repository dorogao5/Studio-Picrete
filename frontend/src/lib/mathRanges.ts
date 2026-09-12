export interface MathRange { from: number; to: number; body: string; display: boolean }
export function mathRanges(text: string): MathRange[] {
  const ranges: MathRange[] = [];
  const tokens = /```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)(?:\\.|[^$\n\\])+\$/g;
  for (const match of text.matchAll(tokens)) {
    const raw = match[0];
    if (raw.startsWith('`') || raw.startsWith('~')) continue;
    const width = raw.startsWith('\\') || raw.startsWith('$$') ? 2 : 1;
    ranges.push({ from: match.index!, to: match.index! + raw.length,
      body: raw.slice(width, -width), display: raw.startsWith('$$') || raw.startsWith('\\[') });
  }
  return ranges;
}
