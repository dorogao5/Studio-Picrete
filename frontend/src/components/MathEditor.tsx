import "katex/contrib/mhchem";
import { useEffect, useRef, type KeyboardEventHandler } from 'react';
import { Compartment, EditorState, StateEffect, StateField } from '@codemirror/state';
import { EditorView, Decoration, WidgetType, keymap, placeholder, showTooltip, type DecorationSet, type Tooltip } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { mathRanges, type MathRange } from '../lib/mathRanges';
const focused = StateEffect.define<boolean>();
const focusField = StateField.define<boolean>({ create: () => false,
  update: (value, tr) => tr.effects.reduce((v, e) => e.is(focused) ? e.value : v, value) });
function renderFormula(body: string, display: boolean) {
  const dom = document.createElement('span');
  dom.className = 'math-editor-formula';
  katex.render(body, dom, { displayMode: display, throwOnError: false, trust: false, strict: 'ignore' });
  return dom;
}
class Formula extends WidgetType {
  constructor(readonly range: MathRange) { super(); }
  eq(other: Formula) { return this.range.body === other.range.body && this.range.from === other.range.from && this.range.display === other.range.display; }
  toDOM(view: EditorView) {
    const dom = renderFormula(this.range.body, this.range.display);
    dom.title = 'Нажмите, чтобы изменить формулу';
    dom.addEventListener('mousedown', event => {
      event.preventDefault();
      const width = this.range.display || view.state.doc.sliceString(this.range.from, this.range.from + 1) === '\\' ? 2 : 1;
      view.dispatch({ selection: { anchor: this.range.from + width }, effects: focused.of(true) });
      view.focus();
    });
    return dom;
  }
  ignoreEvent() { return true; }
}
function selected(state: EditorState, range: MathRange) {
  return state.field(focusField) && state.selection.ranges.some(s => s.from <= range.to && s.to >= range.from);
}
function decorations(state: EditorState) {
  return Decoration.set(mathRanges(state.doc.toString())
    .map(r => (selected(state, r) ? Decoration.mark({ class: "cm-math-source" }) : Decoration.replace({ widget: new Formula(r) })).range(r.from, r.to)));
}
const formulas = StateField.define<DecorationSet>({ create: decorations,
  update: (value, tr) => tr.docChanged || tr.selection || tr.effects.some(e => e.is(focused)) ? decorations(tr.state) : value,
  provide: field => EditorView.decorations.from(field) });
const preview = StateField.define<readonly Tooltip[]>({ create: () => [],
  update(_, tr) {
    if (!tr.state.field(focusField)) return [];
    const head = tr.state.selection.main.head;
    const range = mathRanges(tr.state.doc.toString()).find(r => head >= r.from && head <= r.to);
    return range ? [{ pos: range.from, above: true, strictSide: true, create: () => ({ dom: renderFormula(range.body, range.display) }) }] : [];
  }, provide: field => showTooltip.computeN([field], state => state.field(field)) });
const theme = EditorView.theme({
  '&': { border: '1px solid hsl(var(--border))', borderRadius: '0.5rem', background: 'hsl(var(--background))', fontSize: '1rem' },
  '&.cm-focused': { outline: '2px solid hsl(var(--ring))', outlineOffset: '2px' },
  '.cm-scroller': { fontFamily: 'inherit', overflow: 'auto', minHeight: '12rem', maxHeight: '32rem' },
  '.cm-content': { padding: '12px', overflowWrap: 'anywhere' },
  '.cm-line': { padding: '0', lineHeight: '1.7' },
  '.math-editor-formula': { cursor: 'text', display: 'inline-block', maxWidth: '100%', overflowX: 'auto', verticalAlign: 'middle' },
  '.cm-tooltip': { padding: '8px 12px', maxWidth: 'min(90vw, 800px)', maxHeight: '240px', overflow: 'auto', background: 'hsl(var(--background))', borderRadius: '8px', boxShadow: '0 3px 14px #0003', zIndex: '100' },
  '.cm-math-source': { background: 'hsl(var(--muted))', color: 'hsl(var(--accent-foreground))', fontFamily: 'monospace', borderRadius: '3px' },
  '.katex-display': { margin: '0.35em 0' } });
export default function MathEditor({ value, onChange, label = 'Решение студента', hint = 'Введите решение или загрузите фото', disabled = false, className = '', onKeyDown, maxLength = 60000 }: {
  value: string; onChange: (value: string) => void; label?: string; hint?: string; disabled?: boolean; className?: string; onKeyDown?: KeyboardEventHandler<HTMLDivElement>; maxLength?: number;
}) {
  const parent = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  const initial = useRef(value);
  const editable = useRef(new Compartment());
  useEffect(() => {
    const editor = new EditorView({ parent: parent.current!, state: EditorState.create({ doc: initial.current, extensions: [
      editable.current.of([EditorState.readOnly.of(disabled), EditorView.editable.of(!disabled)]),
      EditorState.transactionFilter.of(tr => tr.newDoc.length > maxLength && tr.docChanged ? [] : tr),
      history(), keymap.of([...defaultKeymap, ...historyKeymap]), markdown(), syntaxHighlighting(defaultHighlightStyle),
      EditorView.lineWrapping, placeholder(hint), focusField, formulas, preview, theme,
      EditorView.contentAttributes.of({ 'aria-label': label, role: 'textbox', 'aria-multiline': 'true' }),
      EditorView.domEventHandlers({ focus: (_, v) => { v.dispatch({ effects: focused.of(true) }); }, blur: (_, v) => { v.dispatch({ effects: focused.of(false) }); } }),
      EditorView.updateListener.of(update => { if (update.docChanged) change.current(update.state.doc.toString()); }),
    ] }) });
    view.current = editor;
    return () => { view.current = null; editor.destroy(); };
  }, [label, hint, maxLength]);
  useEffect(() => { view.current?.dispatch({ effects: editable.current.reconfigure([EditorState.readOnly.of(disabled), EditorView.editable.of(!disabled)]) }); }, [disabled]);
  useEffect(() => {
    const editor = view.current;
    if (editor && editor.state.doc.toString() !== value) editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
  }, [value]);
  return <div onKeyDown={onKeyDown} className={`min-w-0 flex-1 space-y-2 ${className}`}><div ref={parent} /><p className="text-xs text-muted-foreground">Нажмите на формулу для редактирования — её предпросмотр появится сверху. Вне редактирования формулы отображаются в обычном виде.</p></div>;
}
