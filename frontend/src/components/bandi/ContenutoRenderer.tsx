import type { ContenutoSection, ContenutoSegment } from "../../types";

/** Rende un segmento di testo (text/bold/link) come elementi React puri:
 *  il contenuto arriva come JSON strutturato, mai come HTML. */
function Segment({ segment }: { segment: ContenutoSegment }) {
  const text = segment.text ?? "";
  if (segment.kind === "bold") return <strong className="font-semibold text-slate-900">{text}</strong>;
  if (segment.kind === "link" && segment.href) {
    return (
      <a
        href={segment.href}
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
    case "list":
      return (
        <ul className="mt-3 list-disc space-y-1.5 pl-6 text-slate-600">
          {(section.items ?? []).map((item, i) => (
            <li key={i} className="leading-relaxed">
              {typeof item === "string" ? item : item.text ?? <Segments segments={item.segments} />}
            </li>
          ))}
        </ul>
      );
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
