import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type ToolTier = "jina" | "exa" | "firecrawl" | "harness-native";

export type ToolLadder = {
  tier: ToolTier;
  present: ToolTier[];
  note: string;
};

function mcpConfigExists(filename: string): boolean {
  return existsSync(join(homedir(), filename));
}

/**
 * Harness-native floor (WebSearch/WebFetch) always. Upgrade to jina / exa / firecrawl
 * when their MCP config is present. Detection is presence, not a live call.
 */
export function detectToolLadder(env: NodeJS.ProcessEnv = process.env): ToolLadder {
  const present: ToolTier[] = ["harness-native"];
  const claude = join(homedir(), ".claude.json");
  const cursor = join(homedir(), ".cursor", "mcp.json");
  const hasJina =
    Boolean(env.JINA_API_KEY) ||
    mcpConfigExists(".claude.json") && true;
  // Presence of the well-known MCP config files that this machine actually uses.
  const claudeExists = existsSync(claude);
  const cursorExists = existsSync(cursor);
  if (claudeExists || env.JINA_API_KEY) present.push("jina");
  if (claudeExists || cursorExists || env.EXA_API_KEY) present.push("exa");
  if (env.FIRECRAWL_API_KEY || existsSync(join(homedir(), ".firecrawl"))) present.push("firecrawl");

  const premium = present.find((t) => t !== "harness-native");
  return {
    tier: premium ?? "harness-native",
    present: [...new Set(present)],
    note: premium
      ? `upgrade: ${premium} detected; harness-native remains the floor`
      : "floor: harness-native WebSearch/WebFetch only",
  };
}
