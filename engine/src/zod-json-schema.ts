import type { z } from "zod";

/** Minimal Zod-3 → JSON Schema so write-graph.py can validate without a second hand-maintained copy. */
export function zodToJsonSchema(schema: z.ZodTypeAny, name: string): Record<string, unknown> {
  return { title: name, ...convert(schema) };
}

function convert(schema: z.ZodTypeAny): Record<string, unknown> {
  const def = (schema as unknown as { _def: { typeName: string; [k: string]: unknown } })._def;
  const t = def.typeName;
  switch (t) {
    case "ZodString":
      return { type: "string" };
    case "ZodNumber":
      return { type: "number" };
    case "ZodBoolean":
      return { type: "boolean" };
    case "ZodUnknown":
    case "ZodAny":
      return {};
    case "ZodLiteral":
      return { const: def.value };
    case "ZodEnum":
      return { type: "string", enum: def.values as string[] };
    case "ZodArray":
      return { type: "array", items: convert(def.type as z.ZodTypeAny) };
    case "ZodOptional":
    case "ZodDefault":
      return convert((def.innerType ?? def.type) as z.ZodTypeAny);
    case "ZodNullable":
      return { anyOf: [convert(def.innerType as z.ZodTypeAny), { type: "null" }] };
    case "ZodRecord":
      return { type: "object", additionalProperties: convert((def.valueType ?? def.innerType) as z.ZodTypeAny) };
    case "ZodObject": {
      const shape = typeof def.shape === "function" ? (def.shape as () => Record<string, z.ZodTypeAny>)() : (def.shape as Record<string, z.ZodTypeAny>);
      const properties: Record<string, unknown> = {};
      const required: string[] = [];
      for (const [k, v] of Object.entries(shape ?? {})) {
        properties[k] = convert(v);
        const inner = (v as unknown as { _def: { typeName: string } })._def.typeName;
        if (inner !== "ZodOptional" && inner !== "ZodDefault") required.push(k);
      }
      return { type: "object", properties, required, additionalProperties: false };
    }
    default:
      return { description: `unmapped zod type ${t}` };
  }
}
