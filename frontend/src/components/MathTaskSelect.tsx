import { useId, useRef, useState } from "react";
import MathText from "./MathText";

/** Task labels remain complete Markdown; clipping happens after rendering. */
export function MathTaskSelect({ value, onChange, options, placeholder, disabled = false }: {
  value: string; onChange: (value: string) => void; options: { id: string; text: string }[]; placeholder: string; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = options.find(option => option.id === value);
  const matches = options.filter(option => option.text.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const choose = (next: string) => { onChange(next); setOpen(false); setQuery(""); trigger.current?.focus(); };
  return <div className="relative min-w-0" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }} onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
    <button ref={trigger} type="button" disabled={disabled} aria-expanded={open} aria-controls={open ? id : undefined} className="w-full rounded-md border border-input bg-card p-3 text-left text-sm" onClick={() => setOpen(!open)}>
      {selected ? <MathText className="line-clamp-2">{selected.text}</MathText> : placeholder}
      <span className="mt-1 block text-xs text-muted-foreground">Открыть список ▾</span>
    </button>
    {open && <div id={id} className="absolute z-40 mt-1 w-full rounded-md border border-border bg-card p-2 shadow-lg">
      <input aria-label="Поиск задачи" value={query} onChange={event => setQuery(event.target.value)} placeholder="Поиск по условию" className="mb-2 w-full rounded border border-input bg-background p-2 text-sm" />
      <div className="max-h-72 overflow-y-auto">
        <button type="button" className="w-full rounded p-2 text-left text-sm hover:bg-muted" onClick={() => choose("")}>{placeholder}</button>
        {matches.slice(0, 60).map(option => <button type="button" key={option.id} aria-pressed={option.id === value} onClick={() => choose(option.id)} className="block w-full rounded p-2 text-left hover:bg-muted"><MathText className="line-clamp-3">{option.text}</MathText></button>)}
        {matches.length > 60 && <p className="p-2 text-xs text-muted-foreground">Показаны первые 60. Уточните поиск.</p>}
        {!matches.length && <p className="p-2 text-sm text-muted-foreground">Задачи не найдены</p>}
      </div>
    </div>}
  </div>;
}
