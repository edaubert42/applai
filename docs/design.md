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

The light theme keeps the dark theme's hues. Every text token reaches 4.5:1 on each background it is drawn on, in both themes; `frontend/src/theme.test.ts` checks it.

- Font: Figtree, self-hosted (`@fontsource-variable/figtree`), never from a CDN.
- Radius: pills `999px` (`rounded-pill`), cards `32px` (`rounded-card`), blocks `20px` (`rounded-block`).
- Touch targets: at least 44 px (`min-h-11`).

## Layout

- **Header** on `surface`: the ApplAI mark (an A in a `primary` rounded square) and its name; in the middle, the pill navigation on a `raised` track, the current page's pill in `primary`; on the right, the theme switch and the language switch.
- **Pages**: À revoir / To review, Candidatures / Applications, Contrôle / Control. Each has its title and, for now, an empty card.
- **Settings**: dark and French by default; the candidate's choice is kept in `localStorage`.
- **Strings**: all in `frontend/src/locales/{fr,en}.json`; the lint fails on a literal string in JSX.
