import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { VIEWS, api, type Card, type Decision, type View } from "./api";
import AddOffer from "./AddOffer";
import DeclinePanel from "./DeclinePanel";
import { Header, Logo, OfferRow, ReviewCard, useSourceName } from "./ReviewCard";
import ScanBar from "./ScanBar";
import { useSettings } from "./settings";

type Lists = Record<View, Card[]>;

const focus = "focus-visible:outline-2 focus-visible:outline-link";
const REREAD_POLL_MS = 5000;

/**
 * The To review screen: the scan, the three views, and the offers with their decisions.
 * To review lists its offers as rows and opens one card at a time; Later shows its cards.
 * onOpenSettings opens Réglages, where a failing service is explained.
 */
export default function ReviewScreen({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { t } = useSettings();
  const [view, setView] = useState<View>("to_review");
  const [lists, setLists] = useState<Lists | null>(null);
  const [failed, setFailed] = useState(false);
  const [problem, setProblem] = useState(false); // a decision the API refused
  const [rereadFailed, setRereadFailed] = useState(false);
  const [declining, setDeclining] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null); // the card open among the rows of To review
  const refocus = useRef<number | null>(null); // the row to give the focus back to once its card closed
  const deciding = useRef(false);
  const visible = useRef(new Map<number, number>()); // the share of each card on screen

  const [loads, setLoads] = useState(0); // each change loads the cards again
  const reload = useCallback(() => setLoads((count) => count + 1), []);

  useEffect(() => {
    let current = true;
    Promise.all(VIEWS.map((name) => api.cards(name))).then(
      ([to_review = [], later = [], screened_out = []]) => {
        if (!current) return;
        setLists({ to_review, later, screened_out });
        setFailed(false);
      },
      () => current && setFailed(true),
    );
    return () => {
      current = false;
    };
  }, [loads]);

  const shown = useMemo(() => lists?.[view] ?? [], [lists, view]);
  const decliningCard = shown.find((card) => card.id === declining);
  const asRows = view === "to_review";
  const openCard = asRows ? shown.find((card) => card.id === open) : undefined;

  function openOnly(id: number | null) {
    setOpen(id);
    setDeclining(null);
  }

  function closeCard(card: Card) {
    refocus.current = card.id;
    openOnly(null);
  }

  useEffect(() => {
    if (open === null && refocus.current !== null) {
      document.querySelector<HTMLElement>(`[data-row-id="${refocus.current}"]`)?.focus();
      refocus.current = null;
    }
    if (open !== null) document.getElementById(`offer-${open}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open]);

  function moveBetweenRows(step: 1 | -1) {
    const all = Array.from(document.querySelectorAll<HTMLElement>("[data-row-id]"));
    const at = all.indexOf(document.activeElement as HTMLElement);
    all[Math.min(Math.max(at + step, 0), all.length - 1)]?.focus();
  }

  // The card in view: the one most on screen, the first when none is known.
  const inView = useCallback(() => {
    let best: Card | undefined;
    for (const card of shown) {
      if (!best || (visible.current.get(card.id) ?? 0) > (visible.current.get(best.id) ?? 0)) best = card;
    }
    return best;
  }, [shown]);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = Number((entry.target as HTMLElement).dataset.offerId);
        if (entry.intersectionRatio > 0) visible.current.set(id, entry.intersectionRatio);
        else visible.current.delete(id);
      }
    });
    document.querySelectorAll("[data-offer-id]").forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [shown, declining]);

  async function decide(card: Card, decision: Decision) {
    if (deciding.current) return;
    deciding.current = true;
    setProblem(false);
    try {
      await api.decide(card.id, decision);
      if (asRows && open === card.id) {
        const at = shown.indexOf(card);
        openOnly((shown[at + 1] ?? shown[at - 1])?.id ?? null); // the next offer, or the one above the last
      }
      setLists((now) => {
        if (!now) return now;
        const without = (cards: Card[]) => cards.filter((other) => other.id !== card.id);
        const later = decision.decision === "later" ? [{ ...card, state: "later" }, ...now.later] : now.later;
        return { to_review: without(now.to_review), later: without(later), screened_out: without(now.screened_out) };
      });
      setDeclining(null);
    } catch {
      setProblem(true);
    } finally {
      deciding.current = false;
    }
  }

  // The extension answers within a minute or so: the cards load again until no read is pending.
  const waiting = lists?.to_review.some((card) => card.rereading) ?? false;
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(reload, REREAD_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, reload]);

  async function reread(card: Card) {
    try {
      await api.reread(card.id);
      setRereadFailed(false);
      reload();
    } catch {
      setRereadFailed(true); // not connected, or not an offer to read again
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (declining !== null || view === "screened_out" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (target && ["TEXTAREA", "INPUT", "SELECT"].includes(target.tagName)) return;
      if (asRows && !openCard) {
        if (event.key === "ArrowDown") moveBetweenRows(1);
        else if (event.key === "ArrowUp") moveBetweenRows(-1);
        else return;
        event.preventDefault();
        return;
      }
      if (openCard && event.key === "Escape") {
        closeCard(openCard);
        event.preventDefault();
        return;
      }
      const card = openCard ?? inView();
      if (!card) return;
      if (event.key === "ArrowRight") void decide(card, { decision: "interested" });
      else if (event.key === "ArrowLeft") setDeclining(card.id);
      else if (event.key === "ArrowDown" && card.state !== "later") void decide(card, { decision: "later" });
      else return;
      event.preventDefault();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  function fullCard(card: Card) {
    return (
      <article
        key={card.id}
        id={`offer-${card.id}`}
        data-offer-id={card.id}
        aria-labelledby={`offer-${card.id}-title`}
        className="space-y-5.5 rounded-card border border-border bg-surface p-7"
      >
        {card.id === decliningCard?.id ? (
          <>
            <Header card={card} compact />
            <DeclinePanel
              onCancel={() => setDeclining(null)}
              onSave={(reasons, note) =>
                void decide(card, { decision: "not_interested", reasons, ...(note ? { note } : {}) })
              }
            />
          </>
        ) : (
          <ReviewCard
            card={card}
            onInterested={() => void decide(card, { decision: "interested" })}
            onNotInterested={() => setDeclining(card.id)}
            onLater={() => void decide(card, { decision: "later" })}
            onReread={() => void reread(card)}
          />
        )}
      </article>
    );
  }

  return (
    <div className="mx-auto max-w-215 space-y-7">
      <ScanBar onScanned={reload} onOpenSettings={onOpenSettings} />
      <AddOffer onAdded={reload} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label={t("review.tabs")} className="flex flex-wrap gap-1 rounded-pill bg-surface p-1">
          {VIEWS.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={name === view}
              onClick={() => {
                setView(name);
                openOnly(null);
              }}
              className={`min-h-11 cursor-pointer rounded-pill px-5 font-medium ${focus} ${
                name === view ? "bg-primary text-on-primary hover:brightness-90" : "text-muted hover:text-text"
              }`}
            >
              {t(`review.tab.${name}`, { n: lists?.[name].length ?? 0 })}
            </button>
          ))}
        </div>
        {view !== "screened_out" && (
          <p className="text-muted">
            {t(shown.length === 1 ? "review.summary.one" : "review.summary.other", { n: shown.length })}
          </p>
        )}
      </div>
      {failed && <p role="alert" className="text-minus">{t("review.error")}</p>}
      {problem && <p role="alert" className="text-minus">{t("decision.error")}</p>}
      {rereadFailed && <p role="alert" className="text-minus">{t("card.rereadRefused")}</p>}
      {view === "screened_out" ? (
        <ScreenedOut cards={shown} onRescue={(card) => void decide(card, { decision: "interested" })} />
      ) : (
        <>
          {lists && shown.length === 0 && <p className="text-center text-muted">{t("review.empty")}</p>}
          {asRows ? (
            <ul className="space-y-3">
              {shown.map((card) => (
                <li key={card.id}>
                  {card.id === openCard?.id ? (
                    fullCard(card)
                  ) : (
                    <OfferRow card={card} onOpen={() => openOnly(card.id)} />
                  )}
                </li>
              ))}
            </ul>
          ) : (
            shown.map(fullCard)
          )}
          {shown.length > 0 && (
            <p className="text-center text-sm text-muted">{t(asRows && !openCard ? "review.keys.rows" : "review.keys")}</p>
          )}
        </>
      )}
    </div>
  );
}

function ScreenedOut({ cards, onRescue }: { cards: Card[]; onRescue: (card: Card) => void }) {
  const { t } = useSettings();
  const sourceName = useSourceName();
  return (
    <section className="space-y-3">
      <h2 className="text-xl font-semibold">{t("screened.title")}</h2>
      <p className="text-muted">{t("screened.hint")}</p>
      <ul className="space-y-3">
        {cards.map((card) => (
          <li key={card.id} className="flex flex-wrap items-center gap-4 rounded-block bg-surface p-4">
            <Logo card={card} className="size-10 shrink-0 rounded-xl" />
            <div className="min-w-0 flex-1 basis-60">
              <h3 className="font-semibold [overflow-wrap:anywhere]">{card.title}</h3>
              <p className="text-sm text-muted [overflow-wrap:anywhere]">
                {[card.company, card.source && sourceName(card.source)].filter(Boolean).join(" · ")}
              </p>
            </div>
            <span className="rounded-pill bg-raised px-3 py-1 text-sm">
              {card.rule ? t("screened.rule", { rule: card.rule }) : t("card.score", { n: card.score ?? 0 })}
            </span>
            <button
              type="button"
              onClick={() => onRescue(card)}
              className={`min-h-11 cursor-pointer rounded-pill border border-outline px-5 font-semibold text-link hover:bg-raised ${focus}`}
            >
              {t("screened.rescue")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
