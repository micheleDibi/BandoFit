import { AxiosError } from "axios";
import { api } from "./api";

/** Con `responseType: "blob"` anche il corpo d'ERRORE arriva come Blob, quindi
 *  `apiErrorMessage` (che legge un JSON) non lo vede. Qui lo leggiamo e ne
 *  estraiamo `error.message` del backend, così il chiamante può mostrare il
 *  messaggio vero (es. «Generazione PDF non disponibile») invece di un generico. */
async function messageFromBlobError(err: unknown): Promise<string | null> {
  if (!(err instanceof AxiosError) || !(err.response?.data instanceof Blob)) return null;
  try {
    const text = await err.response.data.text();
    const body = JSON.parse(text) as { error?: { message?: string } };
    return body?.error?.message ?? null;
  } catch {
    return null;
  }
}

/** Scarica un file da un GET **autenticato** e ne forza il salvataggio.
 *
 *  Passa dal client axios (non da `<a href>`): così l'interceptor inietta
 *  `Authorization` e `X-Active-Company` anche sulla richiesta blob. Il nome
 *  `filename` alimenta l'attributo `download` del link temporaneo. In caso di
 *  errore rilancia un `Error` col messaggio del backend, se disponibile. */
export async function downloadFile(url: string, filename: string): Promise<void> {
  let res;
  try {
    res = await api.get(url, { responseType: "blob" });
  } catch (err) {
    const message = await messageFromBlobError(err);
    throw new Error(message ?? "Download non riuscito. Riprova.");
  }
  const blobUrl = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(blobUrl);
}
