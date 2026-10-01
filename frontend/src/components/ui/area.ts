import {
  Building2,
  CalendarDays,
  CircleUser,
  FileText,
  House,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Users,
  type LucideIcon,
} from "lucide-react";

/** Le aree dell'app: ognuna ha il suo colore (docs/design-system.md, «Colori
 *  d'area»). `home` non ha un colore proprio: usa il navy del marchio. */
export type Area =
  | "home"
  | "bandi"
  | "aicheck"
  | "partenariati"
  | "consulenze"
  | "scadenze"
  | "azienda"
  | "account"
  | "admin";

/** Tutte le aree, nell'ordine della barra laterale. */
export const AREE: readonly Area[] = [
  "home",
  "bandi",
  "aicheck",
  "scadenze",
  "partenariati",
  "consulenze",
  "azienda",
  "account",
  "admin",
];

/** Classi Tailwind di un'area, scritte per intero (Tailwind le trova solo così:
 *  mai `bg-area-${area}`). */
export interface ClassiArea {
  /** Fondo pieno nel colore dell'area (`bg-area-bandi`): punti, barre, segni. Mai sotto un testo. */
  base: string;
  /** Fondo tenue (`bg-area-bandi-soft`): chip delle icone, pillole, tessere. */
  soft: string;
  /** Testo e icone nel colore scuro dell'area (`text-area-bandi-ink`): ≥ 5,2:1 sul soft e sul bianco. */
  ink: string;
  /** Bordo sinistro di 4px nel colore dell'area (larghezza compresa). */
  bordo: string;
  /** Testo sul soft: fondo soft + testo ink insieme (pillole, etichette). */
  suSoft: string;
  /** Tratto SVG nel colore base (`stroke-…`): anelli e linee dei grafici. */
  tratto: string;
  /** Riempimento SVG nel colore base (`fill-…`): barre dei grafici. */
  riempimento: string;
}

const CLASSI: Record<Area, ClassiArea> = {
  home: {
    base: "bg-navy-800",
    soft: "bg-navy-200",
    ink: "text-navy-900",
    bordo: "border-l-4 border-l-navy-800",
    suSoft: "bg-navy-200 text-navy-900",
    tratto: "stroke-navy-800",
    riempimento: "fill-navy-800",
  },
  bandi: {
    base: "bg-area-bandi",
    soft: "bg-area-bandi-soft",
    ink: "text-area-bandi-ink",
    bordo: "border-l-4 border-l-area-bandi",
    suSoft: "bg-area-bandi-soft text-area-bandi-ink",
    tratto: "stroke-area-bandi",
    riempimento: "fill-area-bandi",
  },
  aicheck: {
    base: "bg-area-aicheck",
    soft: "bg-area-aicheck-soft",
    ink: "text-area-aicheck-ink",
    bordo: "border-l-4 border-l-area-aicheck",
    suSoft: "bg-area-aicheck-soft text-area-aicheck-ink",
    tratto: "stroke-area-aicheck",
    riempimento: "fill-area-aicheck",
  },
  partenariati: {
    base: "bg-area-partenariati",
    soft: "bg-area-partenariati-soft",
    ink: "text-area-partenariati-ink",
    bordo: "border-l-4 border-l-area-partenariati",
    suSoft: "bg-area-partenariati-soft text-area-partenariati-ink",
    tratto: "stroke-area-partenariati",
    riempimento: "fill-area-partenariati",
  },
  consulenze: {
    base: "bg-area-consulenze",
    soft: "bg-area-consulenze-soft",
    ink: "text-area-consulenze-ink",
    bordo: "border-l-4 border-l-area-consulenze",
    suSoft: "bg-area-consulenze-soft text-area-consulenze-ink",
    tratto: "stroke-area-consulenze",
    riempimento: "fill-area-consulenze",
  },
  scadenze: {
    base: "bg-area-scadenze",
    soft: "bg-area-scadenze-soft",
    ink: "text-area-scadenze-ink",
    bordo: "border-l-4 border-l-area-scadenze",
    suSoft: "bg-area-scadenze-soft text-area-scadenze-ink",
    tratto: "stroke-area-scadenze",
    riempimento: "fill-area-scadenze",
  },
  azienda: {
    base: "bg-area-azienda",
    soft: "bg-area-azienda-soft",
    ink: "text-area-azienda-ink",
    bordo: "border-l-4 border-l-area-azienda",
    suSoft: "bg-area-azienda-soft text-area-azienda-ink",
    tratto: "stroke-area-azienda",
    riempimento: "fill-area-azienda",
  },
  account: {
    base: "bg-area-account",
    soft: "bg-area-account-soft",
    ink: "text-area-account-ink",
    bordo: "border-l-4 border-l-area-account",
    suSoft: "bg-area-account-soft text-area-account-ink",
    tratto: "stroke-area-account",
    riempimento: "fill-area-account",
  },
  admin: {
    base: "bg-area-admin",
    soft: "bg-area-admin-soft",
    ink: "text-area-admin-ink",
    bordo: "border-l-4 border-l-area-admin",
    suSoft: "bg-area-admin-soft text-area-admin-ink",
    tratto: "stroke-area-admin",
    riempimento: "fill-area-admin",
  },
};

/** Le classi statiche di un'area: `{ base, soft, ink, bordo, suSoft, tratto, riempimento }`. */
export function areaClassi(area: Area): ClassiArea {
  return CLASSI[area];
}

/** L'icona di ogni area, la stessa della barra laterale (`Sparkles` solo per l'AI-check). */
export const areaIcona: Record<Area, LucideIcon> = {
  home: House,
  bandi: FileText,
  aicheck: Sparkles,
  partenariati: Users,
  consulenze: MessageSquare,
  scadenze: CalendarDays,
  azienda: Building2,
  account: CircleUser,
  admin: ShieldCheck,
};

/** Tono dei grafici (`ProgressRing`, `BarChart`): uno stato o un'area. */
export type TonoGrafico = "fit" | "accent" | "warm" | Area;

const TRATTI_STATO: Record<"fit" | "accent" | "warm", string> = {
  fit: "stroke-fit",
  accent: "stroke-accent",
  warm: "stroke-warm",
};

const RIEMPIMENTI_STATO: Record<"fit" | "accent" | "warm", string> = {
  fit: "fill-fit",
  accent: "fill-accent",
  warm: "fill-warm",
};

function eStato(tono: TonoGrafico): tono is "fit" | "accent" | "warm" {
  return tono === "fit" || tono === "accent" || tono === "warm";
}

/** Classe `stroke-*` del tono (anelli). */
export function trattoGrafico(tono: TonoGrafico): string {
  return eStato(tono) ? TRATTI_STATO[tono] : CLASSI[tono].tratto;
}

/** Classe `fill-*` del tono (barre). */
export function riempimentoGrafico(tono: TonoGrafico): string {
  return eStato(tono) ? RIEMPIMENTI_STATO[tono] : CLASSI[tono].riempimento;
}
