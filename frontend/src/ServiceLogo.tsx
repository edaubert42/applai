import type { ReactNode } from "react";
import type { Category } from "./api";
import { Logo } from "./ReviewCard";
import chrome from "./service-logos/chrome.png";
import claude from "./service-logos/claude.png";
import google from "./service-logos/google.png";
import hellowork from "./service-logos/hellowork.ico";
import jobteaser from "./service-logos/jobteaser.png";
import lesjeudis from "./service-logos/lesjeudis.png";
import linkedin from "./service-logos/linkedin.png";
import notion from "./service-logos/notion.png";
import outlook from "./service-logos/outlook.jpg";
import wttj from "./service-logos/wttj.png";

// The icons are files of this repository: the page asks no other site for them.
const PICTURES: Record<string, string> = {
  extension: chrome,
  alert_linkedin: linkedin,
  alert_wttj: wttj,
  alert_hellowork: hellowork,
  alert_jobteaser: jobteaser,
  alert_lesjeudis: lesjeudis,
  alert_google: google,
  mailbox: outlook,
  notion,
};

// ApplAI's own services, drawn: a search, a person, an archive box.
const DRAWINGS: Record<string, ReactNode> = {
  scan: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  profile: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
  backup: (
    <>
      <rect x="3" y="4" width="18" height="5" rx="1" />
      <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
    </>
  ),
};

const box = "size-8 shrink-0 rounded-lg";
const paper = "bg-[#fff]"; // logos are drawn for white, in either theme; the theme has no white token

/**
 * A service's logo, decorative since its name follows: its site's, Claude's for every agent, a drawing for
 * ApplAI's own, the name's initials for a service this file does not know (a new source).
 */
export function ServiceLogo({ id, category, name }: { id: string; category: Category; name: string }) {
  const key = category === "agents" ? "claude" : id;
  const picture = category === "agents" ? claude : PICTURES[id];
  if (picture) return <img data-logo={key} src={picture} alt="" className={`${box} ${paper} object-contain p-0.5`} />;
  const drawing = DRAWINGS[id];
  if (drawing) {
    return (
      <svg
        data-logo={key}
        aria-hidden="true"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={`${box} bg-raised p-1.5 text-link`}
      >
        {drawing}
      </svg>
    );
  }
  return (
    <span data-logo={key} className="shrink-0">
      <Logo card={{ company: name, logo: null }} className={`${box} text-xs`} />
    </span>
  );
}
