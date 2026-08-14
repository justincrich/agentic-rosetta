Read what broke, how often, and what the fix forbids. This class yields `x` (wrong → right).

From CHANGELOG, MIGRATION, troubleshooting, issue exports, postmortems:

- each scar is an anti-pattern pair: the thing someone did (`key` / wrong) → the thing the fix requires (`value` / right)
- say how often it recurred if the source says so
- a deprecation is an `x` tagged with the version it died

Do not turn a feature announcement into a scar. "Added foo" is not an anti-pattern. "Removed foo because it silently dropped events" is.
