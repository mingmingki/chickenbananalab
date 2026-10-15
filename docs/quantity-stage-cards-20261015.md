# Quantity: inner cards of the guided stage — 2026-10-15

Follows the job resume increment on the same branch (`feat/quantity-ops-and-stage-20261015`). The guided stage introduced a wide light sheet, 14–15px text and a teal accent. Before this change, the cards inside it still used the old 360px messenger popup: fixed widths of 260–320px and 10–11px text. The drawing review window and the 3D view did not match either.

## Stage layout fix
- **The bug:** the two decorative glows behind the stage reached past the screen edge (15% sideways, 38% down). The stage screen was `overflow: hidden`, so it was still a scroll container. As soon as anything scrolled it, the whole stage shifted left by about 200px and up, cutting off the header and the sheet. Showing a card or opening the saved jobs list is enough to trigger it.
- **The fix:** the glows now stay inside the screen, and the screen is `overflow: clip`, so it can no longer scroll.

## Chat cards (core/static/core/css/quantity_stage_cards.css)
- **Width:** cards fill the sheet up to 640px instead of 260–320px. This covers the summary, the floor breakdown, the drawing previews, the required and missing items, the overview, the spec, progress, file boxes and saved jobs. On phones, cards use the full width.
- **Text:** titles are 14.5px and body text is 13–13.5px.
- **Floor breakdown:** each floor is a soft row, and each section heading uses the accent colour.
- **Progress:** the bar uses the stage gradient.
- **Actions:** buttons sit side by side and are 46px high. The primary button uses the stage accent.
- **Saved jobs:** shown as a card with full-size buttons.

## Drawing review window
- **Frame:** a gradient header like the stage, and the incomplete-extraction notice as an amber panel.
- **Checklist:** a 400px column of white item cards with 14px marks and a pill-shaped "도면에서 보기" link.
- **Drawing evidence:** the lines for the CAD plan, schedule, measured length and storey height comparisons previously had no style and showed in large default text. Each is now a compact line with a bold label. A line that reports a difference is amber.
- **Edit fields and buttons:** 13px edit fields, and a 46px proceed button.
- **Phones:** the window is full screen, the checklist sits above the drawing, and the proceed button takes the full width.

## 3D massing view
- **Styles:** the inline styles moved to classes.
- **Look:** the totals and the colour legend sit on a stage-gradient band, controls are 12.5px with 32px fields, and the background matches the review viewer. On phones the view is full screen.

## Other
- **Message order:** an error message now appears before the "2. 프로젝트 개요 확인" button that follows it. Before, the button came first.
- **Cache:** changed static files carry a version query, so browsers fetch the new stylesheet and script after the deploy.

## Checked
Checked locally with fixture jobs (no AI calls; no AI key is configured locally):
- the saved jobs list, resuming an interrupted job, the review window, the calculated results and the 3D view, at 1280×860 and at phone width;
- the stage stays in place (no sideways scroll), there is no page-wide horizontal overflow, and the console shows no errors.

Full core suite passes (742 tests).
