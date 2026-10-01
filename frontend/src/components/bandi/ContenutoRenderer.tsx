import type { ContenutoItem, ContenutoSection, ContenutoSegment } from "../../types";
import { Accordion } from "../ui/Accordion";
import { TextLink } from "../ui/TextLink";

/** Rende un segmento di testo (text/bold/link) come elementi React puri:
 *  il contenuto arriva come JSON strutturato, mai come HTML. */
function Segment({ segment }: { segment: ContenutoSegment }) {
  const text = segment.text ?? "";
  // Nei dati reali l'URL dei link vive in `url` (in `href` nelle versioni più vecchie).
  const grezzo = segment.href ?? segment.url;
  const link = typeof grezzo === "string" ? grezzo.trim() : "";
  if (segment.kind === "bold") return <strong className="font-semibold text-ink">{text}</strong>;
  // Si rende come link solo un indirizzo http(s): il resto resta testo semplice.
  if (segment.kind === "link" && /^https?:\/\//i.test(link)) {
    return (
      <TextLink href={link} esterno>
        {text}
      </TextLink>
    );
  }
  return <>{text}</>;
}

function Segments({ segments }: { segments?: ContenutoSegment[] }) {
  if (!segments?.length) return null;
  return (
    <>
      {segments.map((seg, i) => (
        <Segment key={i} segment={seg} />
      ))}
    </>
  );
}

function ItemContent({ item }: { item: string | ContenutoItem }) {
  if (typeof item === "string") return <>{item}</>;
  if (item.segments?.length) return <Segments segments={item.segments} />;
  return <>{item.text ?? ""}</>;
}

function ListSection({ section, ordered }: { section: ContenutoSection; ordered: boolean }) {
  const items = section.items ?? [];
  if (!items.length) return null;
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag
      className={`flex flex-col gap-1.5 pl-5 text-prose text-ink ${ordered ? "list-decimal" : "list-disc"}`}
    >
      {items.map((item, i) => (
        <li key={i}>
          <ItemContent item={item} />
        </li>
      ))}
    </Tag>
  );
}

/** Domande e risposte come sezioni richiudibili (`Accordion`). */
function FaqSection({ section, indice }: { section: ContenutoSection; indice: number }) {
  const items = (section.items ?? []).filter(
    (item): item is ContenutoItem => typeof item !== "string" && !!item.q,
  );
  if (!items.length) return null;
  return (
    <Accordion
      items={items.map((item, i) => {
        const answer = item.a;
        return {
          id: `faq-${indice}-${i}`,
          titolo: item.q,
          children: (
            <p className="text-prose text-ink-2">
              {typeof answer === "string" ? (
                answer
              ) : answer?.segments?.length ? (
                <Segments segments={answer.segments} />
              ) : (
                answer?.text ?? ""
              )}
            </p>
          ),
        };
      })}
    />
  );
}

function Section({ section, indice }: { section: ContenutoSection; indice: number }) {
  switch (section.type) {
    case "h2":
      return (
        <h2 className="pt-3 text-title-section text-ink">
          {section.text ?? <Segments segments={section.segments} />}
        </h2>
      );
    case "h3":
      return (
        <h3 className="pt-1 text-row-title text-ink">
          {section.text ?? <Segments segments={section.segments} />}
        </h3>
      );
    // Il catalogo usa `bullet_list`/`numbered_list` (e `list` nelle versioni
    // più vecchie): senza questi case gli elenchi sparirebbero dalla pagina.
    case "list":
    case "bullet_list":
      return <ListSection section={section} ordered={false} />;
    case "numbered_list":
      return <ListSection section={section} ordered />;
    case "faq":
      return <FaqSection section={section} indice={indice} />;
    case "paragraph":
    default: {
      const content = section.segments?.length ? (
        <Segments segments={section.segments} />
      ) : (
        section.text
      );
      if (!content) return null;
      return <p className="text-prose text-ink">{content}</p>;
    }
  }
}

/** Il testo lungo del bando: titoli di sezione, paragrafi in `prose`, elenchi
 *  e domande frequenti, con gap 16 (righe sotto gli 80 caratteri a 680px). */
export function ContenutoRenderer({ sections }: { sections?: ContenutoSection[] }) {
  if (!sections?.length) return null;
  return (
    <div className="flex flex-col gap-4">
      {sections.map((section, i) => (
        <Section key={i} section={section} indice={i} />
      ))}
    </div>
  );
}
