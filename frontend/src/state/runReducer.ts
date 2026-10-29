/**
 * Folds the agent event stream into a renderable timeline.
 * Used both for live runs and for replaying stored turns, so they look identical.
 */
import type { AgentEvent, Usage } from "../api/types";

export type ToolStatus = "drafting" | "running" | "done" | "error";

export type Block =
  | { kind: "thinking"; key: string; step: number; text: string }
  | { kind: "text"; key: string; step: number; text: string }
  | {
      kind: "tool";
      key: string;
      step: number;
      id: string;
      name: string;
      status: ToolStatus;
      input: Record<string, unknown> | null;
      summary?: string;
      ui?: Record<string, unknown>;
    };

export type TurnStatus = "streaming" | "done" | "error" | "stopped";

export interface TurnState {
  question: string;
  blocks: Block[];
  status: TurnStatus;
  errors: string[];
  model?: string;
  stats?: { usage: Usage | null; elapsedMs: number; steps: number; stopReason: string | null };
}

export function newTurn(question: string): TurnState {
  return { question, blocks: [], status: "streaming", errors: [] };
}

let seq = 0;
const key = () => `b${++seq}`;

function appendText(blocks: Block[], kind: "thinking" | "text", step: number, text: string): Block[] {
  const last = blocks[blocks.length - 1];
  if (last && last.kind === kind && last.step === step) {
    return [...blocks.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...blocks, { kind, key: key(), step, text }];
}

function updateTool(blocks: Block[], id: string, patch: Partial<Extract<Block, { kind: "tool" }>>): Block[] {
  return blocks.map((b) => (b.kind === "tool" && b.id === id ? { ...b, ...patch } : b));
}

export function reduceTurn(state: TurnState, ev: AgentEvent): TurnState {
  switch (ev.type) {
    case "run_start":
      return { ...state, model: ev.model };
    case "thinking_start":
      return { ...state, blocks: [...state.blocks, { kind: "thinking", key: key(), step: ev.step, text: "" }] };
    case "thinking_delta":
      return { ...state, blocks: appendText(state.blocks, "thinking", ev.step, ev.text) };
    case "text_start":
      return { ...state, blocks: [...state.blocks, { kind: "text", key: key(), step: ev.step, text: "" }] };
    case "text_delta":
      return { ...state, blocks: appendText(state.blocks, "text", ev.step, ev.text) };
    case "tool_start":
      return {
        ...state,
        blocks: [
          ...state.blocks,
          { kind: "tool", key: key(), step: ev.step, id: ev.id, name: ev.name, status: "drafting", input: null },
        ],
      };
    case "tool_call": {
      const exists = state.blocks.some((b) => b.kind === "tool" && b.id === ev.id);
      const blocks = exists
        ? updateTool(state.blocks, ev.id, { status: "running", input: ev.input })
        : [
            ...state.blocks,
            {
              kind: "tool" as const,
              key: key(),
              step: ev.step,
              id: ev.id,
              name: ev.name,
              status: "running" as const,
              input: ev.input,
            },
          ];
      return { ...state, blocks };
    }
    case "tool_result":
      return {
        ...state,
        blocks: updateTool(state.blocks, ev.id, {
          status: ev.is_error ? "error" : "done",
          summary: ev.summary,
          ui: ev.ui,
        }),
      };
    case "retry":
      // The stream for this step broke and is being re-requested: drop its partial output.
      return { ...state, blocks: state.blocks.filter((b) => b.step !== ev.step) };
    case "error":
      return { ...state, errors: [...state.errors, ev.message] };
    case "done": {
      const stopped = ev.stop_reason === "cancelled";
      return {
        ...state,
        status: stopped ? "stopped" : ev.ok ? "done" : "error",
        model: ev.model,
        // Tools still drafting when the run ended never executed.
        blocks: state.blocks.filter((b) => !(b.kind === "tool" && b.status === "drafting")),
        stats: { usage: ev.usage, elapsedMs: ev.elapsed_ms, steps: ev.steps, stopReason: ev.stop_reason },
      };
    }
  }
}

export function replayTurn(question: string, events: AgentEvent[]): TurnState {
  return events.reduce(reduceTurn, newTurn(question));
}

/** Steps that called tools: their text is interim narration rather than the final answer. */
export function toolSteps(turn: TurnState): Set<number> {
  return new Set(turn.blocks.filter((b) => b.kind === "tool").map((b) => b.step));
}
