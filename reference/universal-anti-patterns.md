# Universal Anti-Patterns (canonical reference)

These 28 anti-patterns are **language-agnostic machinery**, not language knowledge. They work across ALL technologies — the reviewer adapts them to the target framework's idioms.

This file is the canonical source. The `rosetta-validate` skill carries the same list inline (so it is self-contained even if this file isn't installed), and `rosetta-bootstrap` uses this taxonomy to seed a per-technology probe catalog at `.rosetta/probes/{tech}.md` when it bootstraps a new KB.

## A. State Management

1. **Wrong state primitive for scope** — Using local state for shared data, or shared state for local UI toggle
2. **Direct state mutation from child** — Child component mutating parent state directly instead of emitting events upward
3. **Missing state initialization** — State is null/undefined until first async load, causing flash of empty UI
4. **State split across multiple sources of truth** — Same conceptual state stored in two different variables that drift

## B. Lifecycle / Memory

5. **Resource leak** — Stream subscription, timer, listener, or observer not cleaned up on teardown
6. **Holding reference to destroyed scope** — Retaining reference to a view/viewmodel/component after it's been destroyed
7. **Lifecycle-sensitive API after teardown** — Calling setState/emit/update after unit of work is cancelled

## C. Reactive Data

8. **Blocking operation on UI thread** — Database query, network call, or expensive computation on the main/UI thread
9. **Infinite collection of reactive stream** — Using `.collect`/`.subscribe` on an infinite stream inside a one-shot function
10. **Missing error handling on async/reactive chain** — No `.catch`/error handler on a stream that can fail
11. **Fetch then filter client-side** — Loading all data, then filtering in the view layer instead of pushing the filter to the data layer

## D. Navigation / Routing

12. **Navigating between sibling states** — Using navigation to switch loading/success/error states (these are states, not routes)
13. **Hardcoded route strings** — String paths instead of typed/structured navigation with compile-time safety
14. **Navigation state not restored** — Losing navigation stack on configuration change or process death

## E. Styling / Accessibility

15. **Hardcoded values outside theme** — Colors, spacing, or fonts as literals instead of theme tokens
16. **Missing accessibility labels** — Interactive elements without labels for screen readers
17. **Fixed-size text** — Font sizes that don't scale with user accessibility preferences
18. **Touch target too small** — Interactive elements smaller than the platform's minimum touch size

## F. Dependency Injection

19. **Manual instantiation bypassing DI** — Using `new`/constructor directly instead of DI graph
20. **Service locator / global singleton** — Pulling dependencies from a global registry instead of injection
21. **Constructor injection through entire tree** — Passing dependencies through every layer instead of injecting at point of use

## G. Async / Concurrency

22. **Fire-and-forget without cancellation** — Launching async work without a way to cancel or handle errors
23. **Race condition** — Multiple parallel writes to the same mutable state without synchronization
24. **Deadlock or blocking await** — Synchronously waiting on async work, blocking the calling thread

## H. Completeness

25. **Stub / placeholder in required behavior** — TODO, empty function body, or hardcoded return in production path
26. **Happy-path only** — Implementation handles success but not error, empty, or edge case states
27. **Missing empty state** — No UI for when a list/screen has zero items
28. **Loading state missing or flickers** — No loading indicator, or indicator appears and disappears instantly without showing data
