# Design

The validated mockup, written down. The tokens live in `frontend/src/theme.ts`; `frontend/src/index.css` turns them into Tailwind classes (`bg-surface`, `text-muted`, `rounded-card`…), and Tailwind's own palette is off.

## Tokens

| Token | Dark | Light | Use |
|---|---|---|---|
| `background` | `#1C1D22` | `#F6F7F9` | page |
| `surface` | `#26282F` | `#FFFFFF` | header, cards |
| `raised` | `#2C2F37` | `#EEF0F3` | navigation track, hover |
| `border` | `#3D414B` | `#D5D8DE` | card and header borders |
| `outline` | `#4A4E58` | `#B4B9C2` | button outlines |
| `text` | `#E8EAED` | `#1C1D22` | body text |
| `muted` | `#9AA0A6` | `#5A5F66` | secondary text |
| `primary` / `on-primary` | `#1A73E8` / `#FFFFFF` | same | current pill, main action |
| `link` | `#7DB3FF` | `#1557B0` | links, focus ring |
| `plus` on `plus-bg`, `plus-border` | `#7EE2A0` on `#1D2E24`, `#2E7D46` | `#1E6B3A` on `#E3F5EA`, `#7EC99A` | a point in the offer's favour |
| `minus` on `minus-bg`, `minus-border` | `#FF9A92` on `#33201F`, `#A3403B` | `#A8322C` on `#FCE9E7`, `#E8A39D` | a point against |
| `unknown` on `unknown-bg`, `unknown-border` | `#FFC857` on `#1C1D22`, `#9A6B12` | `#7A5200` on `#FFF6E0`, `#E0B24F` | a doubt |
| `status-green`, `status-orange`, `status-red` | `#4ADE80`, `#FFC857`, `#FF6B61` | `#22A55A`, `#F29D12`, `#E0443C` | a service's light in Réglages, never text |

The light theme keeps the dark theme's hues. Every text token reaches 4.5:1 on each background it is drawn on, in both themes; `frontend/src/theme.test.ts` checks it.

- Font: Figtree, self-hosted (`@fontsource-variable/figtree`), never from a CDN.
- Radius: pills `999px` (`rounded-pill`), cards `32px` (`rounded-card`), blocks `20px` (`rounded-block`).
- Touch targets: at least 44 px (`min-h-11`).

## Layout

- **Header** on `surface`: the ApplAI mark (an A in a `primary` rounded square) and its name; in the middle, the pill navigation on a `raised` track, the current page's pill in `primary`.
- **Réglages**, one column of 860 px: an Apparence block (a sun for Jour, a moon for Nuit) and a separate Langue block, each a pill pair with the current one in `primary`; then, per category, one block per service, like those two: its logo (40 px), its name (under it, on one line, an agent's model and, when not green, what to do), and on the right its state as a coloured light with a halo, the words as its accessible name and tooltip.
- **Pages**: À revoir / To review, Candidatures / Applications, Contrôle / Control. Each has its title and, for now, an empty card.
- **Settings**: dark and French by default; the candidate's choice is kept in `localStorage`.
- **Strings**: all in `frontend/src/locales/{fr,en}.json`; the lint fails on a literal string in JSX.

## To review screen

From top to bottom, in a `max-width` column on `background`:

- **Scan status bar**, a `surface` block (`rounded-block`): a dot (green after a clean scan, red after one with errors), the last scan's time, the number of new offers, the next scan's time and the LinkedIn reads left today; the errors in `minus`, with a link when the fix is the candidate's (reconnect the mailbox). On the right, the pill button "Chercher les offres": outlined and disabled with the time left ("dans 18 min") after a clean scan, `primary` and enabled otherwise.
- **Views**: three pill tabs on a `surface` track, the current one in `primary`, each with its count ("À revoir · 7", "Plus tard · 2", "Écartées · 23"); on the right, "7 offres, triées par score" in `muted`.
- **Rows** (À revoir): one scrolling column at most 860 px wide, so the whole list shows at once. An offer is a row on `surface` (`rounded-block`, 12 px apart, wrapping on a phone): the logo or initials (40 px), the title and under it the company in `link`, the place and the work mode as small tag pills, the score as a `primary` pill. No card is open at first. A click or Enter on a row opens its card in place of the row; one card is open at a time, so opening another closes the first. A decision removes the row and opens the next one (the one above when it was the last). ↑/↓ move the focus between rows; on the open card, → ← ↓ decide and Échap closes it, the focus going back to its row. Under the rows, in `muted`: "Clavier : ↑ ↓ choisir une offre · Entrée : ouvrir sa fiche".
- **Cards**: the open card of À revoir, and every card of Plus tard, which keeps its one scrolling column, 28 px apart. A card is a `surface` block (`rounded-card`, 28 px padding) holding, 22 px apart:
  - a **header frame** on `raised` with a `border` line (radius 22 px): the logo, or the company's initials on a pastel square (56 px, radius 16 px); the job title (24 px, semibold) and under it the company in `link`; the score on the right, a `primary` pill. Under them the tags as small pills on `background`, each with its mark and the colours of its kind: `+` in `plus`, `−` in `minus`, `?` in `unknown`. Last line, in `muted`: "Vue dans une alerte WTTJ, il y a 2 jours · Voir l'offre d'origine" (a link).
  - **L'entreprise**: an uppercase `muted` label (13 px), then a paragraph.
  - **La mission**: the same label, then a bulleted list.
  - **Pour** and **Contre** side by side, each `flex: 1 1 280px` so they stack on a phone: Pour on `plus-bg` with a `plus-border` line, title "+ Pour" in `plus`; Contre on `minus-bg` with a `minus-border` line, title "− Contre" in `minus`, each point led by its mark (`−` in `minus`, `?` in `unknown`).
  - **Three buttons** on one row that wraps, 52 px high: "← Pas intéressé" outlined, "↓ Plus tard" on `raised`, "Intéressé →" in `primary`.
- Under an open card, and under the cards of Plus tard, centred in `muted`: "Clavier : → intéressé · ← pas intéressé · ↓ plus tard · 1 à 7 pour la raison · Échap : refermer".

**Not interested** turns the card into: the header frame, smaller; "Pas intéressé : pourquoi ?" and "Une raison au moins. C'est elle qui apprend quelque chose au Triage."; the seven reasons as pill toggles, each with its key (1 Entreprise, 2 Mission, 3 Expérience, 4 Compétences, 5 Salaire, 6 Lieu, 7 Autre), a chosen one in `primary`; a labelled text area "Préciser (facultatif)"; "Annuler" as a link-coloured text button and "Enregistrer · Entrée" in `primary`, disabled until a reason is chosen. Below: "Touches 1 à 7 : choisir une raison · Entrée : enregistrer · Échap : annuler".

**Écartées** lists one row per offer on `surface` (`rounded-block`): the initials (40 px, on `raised`), the title and under it "company · place · contract · source" in `muted`, the rule or the score as a `raised` pill ("Premier tri : « Senior » dans le titre", "Score 28 : …"), and an outlined "Repêcher" button in `link`. Above the list: the title "Offres écartées" and "Par le premier tri ou par un score bas. Repêcher une offre, c'est corriger le Triage."

## Applications screen

One column in a `max-width` of 860 px, also at 390 px. Four groups, stacked: "En cours", "En attente de vous" (an application at a gate), "Envoyées", "Abandonnées". Each has a heading with its count ("En attente de vous · 2") and one row per application, the latest change first; an empty group says so in `muted`.

A row is the **Écartées** row: a `surface` block (`rounded-block`) holding the company's initials (40 px), the job title and under it the company in `muted`, the state as a `raised` pill, the date of the last change in `muted`, and an outlined "Voir l'offre" link in `link`. An offer the old workflow handles has one row too, in "En cours", with a `muted` "Ancien workflow" pill and its Notion status as written in the state pill. The row wraps on a phone.
