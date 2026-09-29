import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { BandoDetail } from "../types";

/** Porta l'URL del dettaglio sullo slug canonico. Per un bando spostato o unito
 *  a un altro il backend risponde 200 con la scheda aggiornata, il cui `slug` è
 *  diverso da quello richiesto: la scheda va in cache anche sotto lo slug nuovo
 *  (niente secondo caricamento) e l'URL viene sostituito, conservando query e
 *  hash. Nessuna catena: lo slug canonico risponde sempre con sé stesso. */
export function useSlugCanonico(slug: string | undefined, bando: BandoDetail | undefined): void {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const slugCanonico = bando?.slug;

  useEffect(() => {
    if (!slug || !bando || !slugCanonico || slugCanonico === slug) return;
    queryClient.setQueryData(["bando", slugCanonico], bando);
    navigate(`/app/bandi/${slugCanonico}${location.search}${location.hash}`, { replace: true });
    // Solo i due slug: `bando` e `location` si leggono al momento del cambio.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, slugCanonico]);
}
