/** Colore del punteggio di compatibilità AI-check, dal rosso al verde:
 *  0–39 rosso · 40–59 arancione · 60–79 giallo · 80–100 verde. */
export function scoreColorClasses(punteggio: number): { text: string; bar: string } {
  if (punteggio >= 80) return { text: "text-emerald-600", bar: "bg-emerald-500" };
  if (punteggio >= 60) return { text: "text-yellow-600", bar: "bg-yellow-400" };
  if (punteggio >= 40) return { text: "text-orange-600", bar: "bg-orange-400" };
  return { text: "text-red-600", bar: "bg-red-500" };
}
