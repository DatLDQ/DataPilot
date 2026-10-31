export type Cell = string | number | boolean | null;

export interface TableSummary {
  name: string;
  row_count: number;
  column_count: number;
}

export interface DatasetSummary {
  id: string;
  name: string;
  filename: string;
  created_at: string;
  tables: TableSummary[];
}

export interface ColumnProfile {
  name: string;
  type: string;
  min: Cell;
  max: Cell;
  approx_unique: number | null;
  avg: Cell;
  null_pct: Cell;
}

export interface TableProfile {
  name: string;
  row_count: number;
  columns: ColumnProfile[];
  sample: { columns: string[]; rows: Cell[][] };
}

export interface DatasetDetail extends DatasetSummary {
  profile: { tables: TableProfile[] };
}

export interface Conversation {
  id: string;
  dataset_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredTurn {
  id: number;
  question: string;
  events: AgentEvent[];
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  turns: StoredTurn[];
  busy: boolean;
}

export interface Health {
  status: string;
  model: string;
  effort: string;
  provider?: "claude" | "gemini" | "ollama" | "demo";
  api_key_configured: boolean;
  demo_mode: boolean;
}

// ---- tool UI payloads ----------------------------------------------------
export interface SqlResultUI {
  columns: string[];
  column_types: string[];
  rows: Cell[][];
  truncated: boolean;
  elapsed_ms: number;
}

export interface ProfileUI {
  stats: Record<string, Cell | Cell[]>;
  distribution: { columns: string[]; rows: Cell[][] };
  kind: "numeric" | "temporal" | "categorical";
}

export type ChartType = "bar" | "horizontal_bar" | "line" | "area" | "scatter" | "pie";

export interface ChartUI {
  spec: {
    title: string;
    chart_type: ChartType;
    x: string;
    y_keys: string[];
    x_label?: string | null;
    y_label?: string | null;
    description: string | null;
    sql: string;
  };
  data: Record<string, Cell>[];
  truncated: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  cost_usd: number | null;
}

// ---- SSE events (mirrors backend/app/agent.py) ---------------------------
interface Base {
  step: number;
}

export type AgentEvent =
  | (Base & { type: "run_start"; model: string })
  | (Base & { type: "thinking_start" })
  | (Base & { type: "thinking_delta"; text: string })
  | (Base & { type: "text_start" })
  | (Base & { type: "text_delta"; text: string })
  | (Base & { type: "tool_start"; id: string; name: string })
  | (Base & { type: "tool_call"; id: string; name: string; input: Record<string, unknown> | null })
  | (Base & {
      type: "tool_result";
      id: string;
      name: string;
      is_error: boolean;
      summary: string;
      ui: Record<string, unknown>;
    })
  | (Base & { type: "retry"; attempt: number })
  | (Base & { type: "error"; message: string })
  | (Base & {
      type: "done";
      ok: boolean;
      stop_reason: string | null;
      steps: number;
      model: string;
      usage: Usage | null;
      elapsed_ms: number;
    });
