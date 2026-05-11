# Task: Budget

Produce a categorised project budget for the opportunity below at the
target scale. Output as a plain-text table with columns: Item · Amount
(USD) · Notes. End with a Total row.

**Target scale:** $length_constraint
**Funder context:** $why_fits

## Opportunity

$opportunity_summary

## Categories to include (omit any that don't apply; do not invent line items)

- Artist fee
- Materials and equipment (hardware, electronics, fabrication)
- Software (DAW, plugins, fixed-media tools — only if not already owned)
- Documentation (photo, video, audio engineering, editing)
- Travel (only if the opportunity is site-specific or requires presence)
- Accommodation (only if not provided)
- Per diem (only where the funder accepts it)
- Production support / collaborators (name role only; do not invent names)
- Contingency (≤ 10% of subtotal)

Add a one-line "Notes" comment per row explaining the reason for the cost.
If you don't know a market rate for an item, write `[TK: confirm rate]` in
the Amount column. Never invent vendor prices.

$critique
