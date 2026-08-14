Read type-level contracts. This class yields `t`.

From `*.schema.json`, `*.proto`, `*.d.ts`, `types/**`, OpenAPI:

- each exported type / message / endpoint is one fact
- `key` is the type or operation name
- `value` is the signature or the schema's required fields, verbatim enough to implement against
- do not paraphrase a proto into prose if you can quote the field list

A `.d.ts` that is a re-export barrel: record the barrel and pointer the real files, do not invent members.
