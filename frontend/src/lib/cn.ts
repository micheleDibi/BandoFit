import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Unisce classi Tailwind risolvendo i conflitti: le classi passate per ultime
 *  (es. override via `className`) vincono sui default dei variant. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
