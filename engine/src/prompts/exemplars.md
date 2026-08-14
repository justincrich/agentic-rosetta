Read tests and examples as evidence of how the thing is actually used. Yields `a` (call expressions) and `x` (wrong patterns the tests forbid).

- every example call that is the canonical way to do a thing → `a` with the exact expression
- every test that asserts a rejection / throw / lint failure → `x` (the forbidden pattern → the required one)
- do not copy the whole test file. One fact per distinct operation or trap.

A snapshot test with no assertion about behavior is not a fact. Skip it and say why if it was a mustFind.
