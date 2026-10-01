import eduNews24 from "../../assets/edunews24.png";
import { cn } from "../../lib/cn";

/** Attribuzione "powered by EduNews24" con link al sito. */
export function PoweredBy({ className }: { className?: string }) {
  return (
    <a
      href="https://edunews24.it"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Powered by EduNews24"
      className={cn(
        "group inline-flex items-center gap-2 rounded-control",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        className,
      )}
    >
      <span className="text-caption text-ink-3 transition-colors group-hover:text-ink-2">
        powered by
      </span>
      <img
        src={eduNews24}
        alt="EduNews24"
        draggable={false}
        className="h-4 w-auto select-none opacity-80 transition-opacity group-hover:opacity-100"
      />
    </a>
  );
}
