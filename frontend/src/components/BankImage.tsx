import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function BankImage({ url }: { url: string }) {
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setSrc(""); setFailed(false);
    let active = true;
    let objectUrl = "";
    api.get<Blob>(url, { responseType: "blob" }).then(({ data }) => {
      if (active) { objectUrl = URL.createObjectURL(data); setSrc(objectUrl); }
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  if (failed) return <p className="text-xs text-destructive">Не удалось загрузить рисунок к исходной задаче.</p>;
  return src ? <img src={src} alt="Рисунок к условию задачи" className="max-h-80 max-w-full object-contain" /> : <p className="text-xs">Изображение загружается…</p>;
}
