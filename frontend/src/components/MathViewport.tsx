import { useEffect, useRef, useState, type ReactNode } from 'react';

/** Keep a formula intact, with local touch/keyboard scrolling only when needed. */
export function MathViewport({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setOverflowing(element.scrollWidth > element.clientWidth + 4);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    measure();
    document.fonts.addEventListener('loadingdone', measure);
    return () => {
      observer.disconnect();
      document.fonts.removeEventListener('loadingdone', measure);
    };
  }, [children]);
  return <span className={`math-viewport${overflowing ? ' is-overflowing' : ''}`}>
    <span ref={ref} className="math-scroll" tabIndex={overflowing ? 0 : undefined}
      role={overflowing ? 'region' : undefined}
      aria-label={overflowing ? 'Формула с горизонтальной прокруткой' : undefined}>
      {children}
    </span>
    {overflowing && <span className="math-scroll-hint" aria-hidden="true">Прокрутите формулу ↔</span>}
  </span>;
}
