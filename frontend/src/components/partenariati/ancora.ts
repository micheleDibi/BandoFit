/** Ancora e id della sezione «Regole di partenariato» di BandoDetail, in un
 *  modulo a parte: li usano sia la card in sidebar sia la sezione. */
export const PARTENARIATO_ANCORA = "partenariato";
export const PARTENARIATO_TOGGLE_ID = "partenariato-toggle";
export const PARTENARIATO_CONTENUTO_ID = "partenariato-contenuto";

/** Porta la sezione in vista e il focus sul suo bottone (già aperto dal
 *  chiamante): chi usa la tastiera o un lettore di schermo arriva dove si è
 *  spostato lo sguardo. Al frame successivo, quando il contenuto è montato. */
export function vaiASezionePartenariato() {
  requestAnimationFrame(() => {
    const sezione = document.getElementById(PARTENARIATO_ANCORA);
    if (!sezione) return;
    const movimentoRidotto = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    sezione.scrollIntoView({ behavior: movimentoRidotto ? "auto" : "smooth", block: "start" });
    document.getElementById(PARTENARIATO_TOGGLE_ID)?.focus({ preventScroll: true });
  });
}
