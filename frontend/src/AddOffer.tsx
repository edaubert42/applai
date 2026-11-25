import { useState, type FormEvent } from "react";
import { ApiError, api } from "./api";
import { useSettings } from "./settings";

type Outcome = "added" | "known" | "refused" | "error";

/**
 * The button that opens, in place, the field that adds an offer seen outside the watched sources: the
 * next scan reads and triages it. Placed in a wrapping flex row: the open field takes a row of its own.
 */
export default function AddOffer({ onAdded }: { onAdded: () => void }) {
  const { t } = useSettings();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  function add(event: FormEvent) {
    event.preventDefault();
    setOutcome(null);
    api.addOffer(url.trim()).then(
      (offer) => {
        setUrl("");
        setOutcome(offer.added ? "added" : "known");
        onAdded();
      },
      (error: unknown) => setOutcome(error instanceof ApiError && error.status === 422 ? "refused" : "error"),
    );
  }

  const failed = outcome === "refused" || outcome === "error";
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className="min-h-11 cursor-pointer rounded-pill border border-outline px-5 font-semibold text-link hover:bg-raised focus-visible:outline-2 focus-visible:outline-link"
      >
        {t("review.add.open")}
      </button>
      {open && (
        <form onSubmit={add} className="flex basis-full flex-wrap items-center gap-3">
          <input
            type="url"
            required
            aria-label={t("review.add.label")}
            placeholder={`${t("review.add.label")} https://`}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            className="min-h-11 min-w-0 flex-1 basis-60 rounded-pill border border-outline bg-raised px-4"
          />
          <button
            type="submit"
            className="min-h-11 cursor-pointer rounded-pill border border-outline px-5 font-semibold text-link hover:bg-raised focus-visible:outline-2 focus-visible:outline-link"
          >
            {t("review.add.submit")}
          </button>
          {outcome && (
            <p role={failed ? "alert" : "status"} className={`basis-full text-sm ${failed ? "text-minus" : "text-muted"}`}>
              {t(`review.add.${outcome}`)}
            </p>
          )}
        </form>
      )}
    </>
  );
}
