import { ExternalLink } from "lucide-react";
import { forwardRef, type AnchorHTMLAttributes, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { cn } from "../../lib/cn";

interface TextLinkBase {
  children: ReactNode;
  /** Link verso un altro sito: aggiunge l'icona `ExternalLink` e apre in una nuova
   *  scheda con `rel="noopener noreferrer"`. */
  esterno?: boolean;
  className?: string;
}

/** Con `to` è un `<Link>` del router; con `href` un `<a>`. */
export type TextLinkProps =
  | (TextLinkBase & { to: LinkProps["to"]; href?: never } & Omit<LinkProps, "to" | "className">)
  | (TextLinkBase & { href: string; to?: never } & Omit<
        AnchorHTMLAttributes<HTMLAnchorElement>,
        "href" | "className"
      >);

// `inline`, non flex: un link di più parole dentro un paragrafo deve andare a capo
// come il testo intorno, non saltare intero alla riga dopo.
const classi =
  "rounded-mark text-accent-hover underline-offset-2 hover:underline " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

/** Link testuale in `accent-hover`, sottolineato al passaggio. Niente «→» in coda:
 *  il verbo basta; per i link esterni c'è l'icona. */
export const TextLink = forwardRef<HTMLAnchorElement, TextLinkProps>(function TextLink(
  props,
  ref,
) {
  const { children, esterno, className, ...resto } = props;
  const contenuto = (
    <>
      {children}
      {esterno && (
        <ExternalLink className="ml-1 inline-block size-4 align-[-0.125em]" aria-hidden />
      )}
    </>
  );
  // Applicati per ultimi: un `rel` del chiamante si unisce a `noopener noreferrer`
  // invece di sostituirlo (altrimenti il Referer passerebbe al sito esterno).
  const attributiEsterno = esterno ? { target: "_blank", rel: unisciRel(resto.rel) } : {};

  if ("to" in resto && resto.to !== undefined) {
    const { to, href: _href, ...link } = resto;
    return (
      <Link ref={ref} to={to} className={cn(classi, className)} {...link} {...attributiEsterno}>
        {contenuto}
      </Link>
    );
  }
  const { href, to: _to, ...ancora } = resto as Extract<TextLinkProps, { href: string }>;
  return (
    <a ref={ref} href={href} className={cn(classi, className)} {...ancora} {...attributiEsterno}>
      {contenuto}
    </a>
  );
});

function unisciRel(rel: string | undefined): string {
  const voci = new Set((rel ?? "").split(/\s+/).filter(Boolean));
  voci.add("noopener");
  voci.add("noreferrer");
  return [...voci].join(" ");
}
