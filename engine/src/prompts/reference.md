Read the reference docs / remaining markdown / API pages. Yields `w` (components) and `a` (signatures).

- each named building block → `w` with `{ t, i, note }` when those are stated (type, import, what it replaces)
- each documented call → `a` with the exact expression from the page
- if the page claims to be "the API" but is clearly a subset, emit a `surprise` (that claim vs. what the page actually lists)

Do not fill `r` or `x` from marketing language. Enforcement and scars own those sections.
