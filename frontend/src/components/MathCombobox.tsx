import { useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import MathText from "./MathText";

/** Editable topic filter: preserves source values and renders math in suggestions. */
export function MathCombobox({ id, value, onChange, options, placeholder, className = "" }: {
  id?: string; value: string; onChange: (value: string) => void; options: string[];
  placeholder?: string; className?: string;
}) {
  const uid = useId();
  const listId = `${uid}-options`;
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const plain = (s: string) => s.replace(/\\[()[\]]|\$/g, "");
  const matches = options.filter(option => option === value || plain(option).toLocaleLowerCase().includes(plain(value).toLocaleLowerCase()));
  const choices = options.includes(value) ? options : matches;
  const select = (option: string) => { onChange(option); setOpen(false); setActive(-1); };
  return <div className={`relative min-w-0 ${className}`} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) { setOpen(false); setActive(-1); }
  }}>
    <input ref={input} id={id} role="combobox" aria-label={id ? undefined : "Тема"} aria-autocomplete="list"
      aria-expanded={open} aria-controls={open ? listId : undefined}
      aria-activedescendant={open && active >= 0 ? `${uid}-${active}` : undefined}
      autoComplete="off" value={plain(value)} placeholder={placeholder}
      className="h-11 w-full rounded-md border border-input bg-card px-3 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
      onFocus={() => setOpen(true)} onClick={() => setOpen(true)}
      onChange={event => { onChange(event.target.value); setOpen(true); setActive(-1); }}
      onKeyDown={event => {
        if (event.key === "Escape") { setOpen(false); setActive(-1); }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault(); setOpen(true);
          setActive(index => choices.length ? (index < 0 ? (event.key === "ArrowDown" ? 0 : choices.length - 1) : (index + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length) : -1);
        }
        if (event.key === "Enter" && open && active >= 0 && choices[active]) { event.preventDefault(); select(choices[active]); }
      }} />
    <button type="button" tabIndex={-1} aria-label="Показать темы" className="absolute right-0 top-0 flex h-11 w-10 items-center justify-center text-muted-foreground"
      onMouseDown={event => event.preventDefault()} onClick={() => { input.current?.focus(); setOpen(!open); }}><ChevronDown size={16} /></button>
    {open && <div id={listId} role="listbox" aria-label="Темы" className="absolute left-0 right-0 top-full z-50 mt-1 max-h-64 overflow-y-auto rounded-md border border-border bg-card p-1 text-foreground shadow-lg">
      {choices.map((option, index) => <div key={option} id={`${uid}-${index}`} role="option" aria-selected={option === value}
        ref={element => { if (index === active) element?.scrollIntoView({ block: "nearest" }); }}
        className={`cursor-pointer rounded px-3 py-2 text-sm hover:bg-muted ${index === active ? "bg-muted" : ""}`}
        onMouseDown={event => event.preventDefault()} onClick={() => select(option)}><MathText inline>{option}</MathText></div>)}
      {!choices.length && <p className="px-3 py-2 text-sm text-muted-foreground">Нет совпадений. Можно ввести свою тему.</p>}
    </div>}
  </div>;
}
