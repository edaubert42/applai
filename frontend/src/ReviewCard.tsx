import { useState, type ReactNode } from "react";
import type { Card, Point } from "./api";
import { useSettings } from "./settings";

const MARKS = {
  "+": { text: "text-plus", frame: "border-plus-border" },
  "−": { text: "text-minus", frame: "border-minus-border" },
  "?": { text: "text-unknown", frame: "border-unknown-border" },
} as const;

const SIGN = { plus: "+ ", minus: "− " };
const focus ="focus-visible:outline-2 focus-visible:outline-link";
const label = "mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted";

/** The company's initials, as "AR" for Acme Robotics. */
function initials(company: string): string {
  const words = company.split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words.slice(0, 2).map((word) => word[0]).join("") : company.slice(0, 2);
  return letters.toUpperCase();
}

/** The company's logo, or its initials on a pastel square when it has none or it fails. */
export function Logo({ card, className }: { card: Pick<Card, "company" | "logo">; className: string }) {
  const [broken, setBroken] = useState(false);
  if (card.logo && !broken) {
    return (
      <img
        src={`/api/logos/${encodeURIComponent(card.logo)}`}
        alt={card.company}
        onError={() => setBroken(true)}
        // logos are drawn for white, in either theme; the theme has no white token
        className={`${className} bg-[#fff] object-contain`}
      />
    );
  }
  const hue = [...card.company].reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) % 360, 7);
  return (
    <span
      aria-hidden="true"
      style={{ background: `hsl(${hue} 60% 85%)`, color: `hsl(${hue} 50% 25%)` }}
      className={`${className} flex items-center justify-center font-semibold`}
    >
      {initials(card.company)}
    </span>
  );
}

/** Where the watch found an offer, in words; the key itself when it has no name. */
export function useSourceName(): (source: string) => string {
  const { lookup } = useSettings();
  return (source) => lookup(`source.${source}`) ?? source;
}

function Mark({ verdict }: { verdict: Point["verdict"] }) {
  return <span className={`${MARKS[verdict].text} font-bold`}>{verdict}</span>;
}

function Points({ points }: { points: Point[] }) {
  return (
    <ul className="space-y-2">
      {points.map((point) => (
        <li key={point.text} className="flex gap-2">
          <Mark verdict={point.verdict} />
          <span>{point.text}</span>
        </li>
      ))}
    </ul>
  );
}

function Block({ title, tone, children }: { title: ReactNode; tone: string; children: ReactNode }) {
  return (
    <section className={`flex-1 basis-70 min-w-0 rounded-block border p-5 ${tone}`}>
      <h3 className="mb-3 font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/** The framed header of a card: the logo, the title, the company, the score, the tags. */
export function Header({ card, compact = false }: { card: Card; compact?: boolean }) {
  const { t } = useSettings();
  const sourceName = useSourceName();
  return (
    <header className="rounded-[22px] border border-border bg-raised p-5">
      <div className="flex items-center gap-4">
        <Logo card={card} className={`${compact ? "size-10" : "size-14"} shrink-0 rounded-2xl`} />
        <div className="min-w-0 flex-1">
          <h2
            id={`offer-${card.id}-title`}
            className={`${compact ? "text-lg" : "text-2xl"} font-semibold [overflow-wrap:anywhere]`}
          >
            {card.title}
          </h2>
          <p className="text-link [overflow-wrap:anywhere]">{card.company}</p>
        </div>
        {card.score !== null && (
          <span className="rounded-pill bg-primary px-4 py-1 font-semibold text-on-primary">
            {t("card.score", { n: card.score })}
          </span>
        )}
      </div>
      {!compact && card.tags && (
        <ul className="mt-4 flex flex-wrap gap-2">
          {Object.values(card.tags).map((tag) => (
            <li
              key={tag.text}
              className={`flex gap-1.5 rounded-pill border bg-background px-3 py-1 text-sm ${MARKS[tag.verdict].frame}`}
            >
              <Mark verdict={tag.verdict} />
              <span>{tag.text}</span>
            </li>
          ))}
        </ul>
      )}
      {!compact && (
        <p className="mt-4 text-sm text-muted">
          {card.source && <span>{t("card.seen", { source: sourceName(card.source) })}</span>}
          {card.source && " · "}
          <a href={card.url} target="_blank" rel="noreferrer" className="text-link underline hover:opacity-80">
            {t("card.original")}
          </a>
          {!card.full_text_read && " · "}
          {!card.full_text_read && <span>{t("card.fullTextNotRead")}</span>}
        </p>
      )}
    </header>
  );
}

/** An offer as a compact row: the logo, the title, the company, the score, the place and the work mode. */
export function OfferRow({ card, onOpen }: { card: Card; onOpen: () => void }) {
  const { t } = useSettings();
  const tags = [card.tags?.location, card.tags?.work_mode].filter((tag) => tag !== undefined);
  return (
    <button
      type="button"
      data-row-id={card.id}
      aria-expanded="false"
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key !== "Enter") return;
        event.preventDefault(); // the click a browser adds to Enter would open the card twice
        onOpen();
      }}
      className={`flex min-h-11 w-full flex-wrap items-center gap-x-4 gap-y-2 rounded-block bg-surface p-4 text-left hover:bg-raised ${focus}`}
    >
      <Logo card={card} className="size-10 shrink-0 rounded-xl" />
      <span className="min-w-0 flex-1 basis-60">
        <span className="block font-semibold [overflow-wrap:anywhere]">{card.title}</span>
        <span className="block text-sm text-link [overflow-wrap:anywhere]">{card.company}</span>
      </span>
      {tags.map((tag) => (
        <span
          key={tag.text}
          className={`flex gap-1.5 rounded-pill border bg-background px-3 py-1 text-sm ${MARKS[tag.verdict].frame}`}
        >
          <Mark verdict={tag.verdict} />
          <span>{tag.text}</span>
        </span>
      ))}
      {card.score !== null && (
        <span className="rounded-pill bg-primary px-4 py-1 font-semibold text-on-primary">
          {t("card.score", { n: card.score })}
        </span>
      )}
    </button>
  );
}

const button = `min-h-13 flex-1 cursor-pointer rounded-pill px-6 font-semibold ${focus}`;

/** An offer's card, with its three decisions. */
export function ReviewCard({
  card,
  onInterested,
  onNotInterested,
  onLater,
  onReread,
}: {
  card: Card;
  onInterested: () => void;
  onNotInterested: () => void;
  onLater: () => void;
  onReread: () => void;
}) {
  const { t } = useSettings();
  return (
    <>
      <Header card={card} />
      {(card.can_reread || card.rereading || card.reread_error) && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {card.can_reread && (
            <button type="button" onClick={onReread} className={`min-h-11 cursor-pointer rounded-pill border border-outline px-5 font-semibold text-link hover:bg-raised ${focus}`}>
              {t("card.reread")}
            </button>
          )}
          {card.rereading && <span role="status">{t("card.rereading")}</span>}
          {card.reread_error && <span role="alert" className="text-minus">{t("card.rereadError", { reason: card.reread_error })}</span>}
        </div>
      )}
      {card.about && (
        <div>
          <h3 className={label}>{t("card.about")}</h3>
          <p>{card.about}</p>
        </div>
      )}
      {card.mission && (
        <div>
          <h3 className={label}>{t("card.mission")}</h3>
          <ul className="list-disc space-y-1 pl-5">
            {card.mission.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex flex-wrap gap-4">
        <Block
          title={
            <span className="text-plus">
              <span aria-hidden="true">{SIGN.plus}</span>
              {t("card.pros")}
            </span>
          }
          tone="border-plus-border bg-plus-bg"
        >
          {card.pros && <Points points={card.pros.map((text) => ({ text, verdict: "+" as const }))} />}
        </Block>
        <Block
          title={
            <span className="text-minus">
              <span aria-hidden="true">{SIGN.minus}</span>
              {t("card.cons")}
            </span>
          }
          tone="border-minus-border bg-minus-bg"
        >
          {card.cons && <Points points={card.cons} />}
        </Block>
      </div>
      <div className="flex flex-wrap gap-3">
        <button type="button" onClick={onNotInterested} className={`${button} border border-outline hover:bg-raised`}>
          {t("card.notInterested")}
        </button>
        {card.state !== "later" && (
          <button type="button" onClick={onLater} className={`${button} bg-raised hover:bg-border`}>
            {t("card.later")}
          </button>
        )}
        <button type="button" onClick={onInterested} className={`${button} bg-primary text-on-primary hover:brightness-90`}>
          {t("card.interested")}
        </button>
      </div>
    </>
  );
}
