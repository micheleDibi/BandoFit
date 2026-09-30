import { Button } from "./Button";

/** «Pagina N di M» a sinistra, Precedente e Successiva a destra: si scorre
 *  l'elenco, non si salta a una pagina a caso. */
export function Pagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <nav className="flex items-center justify-between gap-4" aria-label="Paginazione">
      <p className="text-small text-ink-2 tabular-nums">
        Pagina {page} di {totalPages}
      </p>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
        >
          Precedente
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => onChange(page + 1)}
          disabled={page >= totalPages}
        >
          Successiva
        </Button>
      </div>
    </nav>
  );
}
