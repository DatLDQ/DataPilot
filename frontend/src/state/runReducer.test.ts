import { describe, expect, it } from "vitest";
import { parseSSEChunk } from "../api/client";
import type { AgentEvent } from "../api/types";
import { newTurn, reduceTurn, replayTurn, toolSteps } from "./runReducer";

const run: AgentEvent[] = [
  { type: "run_start", step: 0, model: "claude-opus-5-5" },
  { type: "thinking_start", step: 1 },
  { type: "thinking_delta", step: 1, text: "Need revenue " },
  { type: "thinking_delta", step: 1, text: "by city." },
  { type: "text_start", step: 1 },
  { type: "text_delta", step: 1, text: "Let me check." },
  { type: "tool_start", step: 1, id: "t1", name: "run_sql" },
  { type: "tool_call", step: 1, id: "t1", name: "run_sql", input: { sql: "SELECT 1", purpose: "x" } },
  { type: "tool_result", step: 1, id: "t1", name: "run_sql", is_error: false, summary: "1 rows", ui: { rows: [[1]] } },
  { type: "text_start", step: 2 },
  { type: "text_delta", step: 2, text: "**HCM** " },
  { type: "text_delta", step: 2, text: "leads." },
  {
    type: "done",
    step: 2,
    ok: true,
    stop_reason: "end_turn",
    steps: 2,
    model: "claude-opus-5-5",
    usage: { input_tokens: 10, output_tokens: 5, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, cost_usd: 0.001 },
    elapsed_ms: 1234,
  },
];

describe("reduceTurn", () => {
  it("builds a timeline from a full run", () => {
    const turn = replayTurn("Which city?", run);
    expect(turn.status).toBe("done");
    expect(turn.blocks.map((b) => b.kind)).toEqual(["thinking", "text", "tool", "text"]);
    expect(turn.blocks[0]).toMatchObject({ text: "Need revenue by city." });
    expect(turn.blocks[2]).toMatchObject({ status: "done", summary: "1 rows", input: { sql: "SELECT 1" } });
    expect(turn.blocks[3]).toMatchObject({ text: "**HCM** leads." });
    expect([...toolSteps(turn)]).toEqual([1]);
    expect(turn.stats?.elapsedMs).toBe(1234);
  });

  it("drops partial output of a retried step", () => {
    let t = newTurn("q");
    for (const ev of run.slice(0, 6)) t = reduceTurn(t, ev);
    t = reduceTurn(t, { type: "retry", step: 1, attempt: 1 });
    expect(t.blocks).toEqual([]);
  });

  it("marks errors and cancelled runs", () => {
    let t = replayTurn("q", [
      { type: "run_start", step: 0, model: "m" },
      { type: "tool_start", step: 1, id: "x", name: "run_sql" },
      { type: "error", step: 1, message: "Stopped." },
      { type: "done", step: 1, ok: false, stop_reason: "cancelled", steps: 1, model: "m", usage: null, elapsed_ms: 0 },
    ]);
    expect(t.status).toBe("stopped");
    expect(t.errors).toEqual(["Stopped."]);
    expect(t.blocks).toEqual([]); // drafting tool never ran
    t = replayTurn("q", [{ type: "done", step: 0, ok: false, stop_reason: null, steps: 0, model: "m", usage: null, elapsed_ms: 0 }]);
    expect(t.status).toBe("error");
  });
});

describe("parseSSEChunk", () => {
  it("yields complete frames and returns the remainder", () => {
    const it = parseSSEChunk('data: {"type":"text_start","step":1}\n\ndata: {"type":"te');
    const first = it.next();
    expect(first.value).toEqual({ type: "text_start", step: 1 });
    const end = it.next();
    expect(end.done).toBe(true);
    expect(end.value).toBe('data: {"type":"te');
  });
});
