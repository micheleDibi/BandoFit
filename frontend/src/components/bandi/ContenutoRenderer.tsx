import type { ContenutoItem, ContenutoSection, ContenutoSegment } from "../../types";

/** Rende un segmento di testo (text/bold/link) come elementi React puri:
 *  il contenuto arriva come JSON strutturato, mai come HTML. */
function Segment({ segment }: { segment: ContenutoSegment }) {
  const text = segment.text ?? "";
  // Nei dati reali l'URL dei link vive in `url` (in `href` nelle versioni più vecchie).
  const link = segment.href ?? segment.url;
  if (segment.kind === "bold") return <strong className="font-semibold text-slate-900">{text}</strong>;
  if (segment.kind === "link" && link) {
    return (
      <a
        href={link}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-brand-600 underline underline-offset-2 hover:text-brand-700"
      >
        {text}
      </a>
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
      className={`mt-3 space-y-1.5 pl-6 text-slate-600 ${ordered ? "list-decimal" : "list-disc"}`}
    >
      {items.map((item, i) => (
        <li key={i} className="leading-relaxed">
          <ItemContent item={item} />
        </li>
      ))}
    </Tag>
  );
}

function FaqSection({ section }: { section: ContenutoSection }) {
  const items = (section.items ?? []).filter(
    (item): item is ContenutoItem => typeof item !== "string" && !!item.q,
  );
  if (!items.length) return null;
  return (
    <div className="mt-4 space-y-3">
      {items.map((item, i) => {
        const answer = item.a;
        return (
          <div key={i} className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <p className="font-medium text-slate-900">{item.q}</p>
            <p className="mt-1.5 leading-relaxed text-slate-600">
              {typeof answer === "string" ? (
                answer
              ) : answer?.segments?.length ? (
                <Segments segments={answer.segments} />
              ) : (
                answer?.text ?? ""
              )}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function Section({ section }: { section: ContenutoSection }) {
  switch (section.type) {
    case "h2":
      return (
        <h2 className="mt-8 font-display text-xl font-semibold text-slate-900 first:mt-0">
          {section.text ?? <Segments segments={section.segments} />}
        </h2>
      );
    case "h3":
      return (
        <h3 className="mt-6 font-display text-lg font-semibold text-slate-900">
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
      return <FaqSection section={section} />;
    case "paragraph":
    default: {
      const content = section.segments?.length ? (
        <Segments segments={section.segments} />
      ) : (
        section.text
      );
      if (!content) return null;
      return <p className="mt-3 leading-relaxed text-slate-600 first:mt-0">{content}</p>;
    }
  }
}

export function ContenutoRenderer({ sections }: { sections?: ContenutoSection[] }) {
  if (!sections?.length) return null;
  return (
    <div>
      {sections.map((section, i) => (
        <Section key={i} section={section} />
      ))}
    </div>
  );
}
