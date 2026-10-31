import clsx from "clsx";
import { AlertTriangle } from "lucide-react";
import { memo, useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChartUI } from "../api/types";
import { layoutAnswer } from "../lib/answer";
import { toolSteps, type Block, type TurnState } from "../state/runReducer";
import { ChartCard } from "./ChartCard";
import { Spinner } from "./ui";

type ToolBlock = Extract<Block, { kind: "tool" }>;

const MarkdownText = memo(function MarkdownText({ text }: { text: string }) {
  return (
    <div className="md">
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
    </div>
  );
});

/** One line saying what the agent is doing, in place of its thinking and tool calls. */
function activity(tool: ToolBlock | undefined): string {
  if (!tool) return "Thinking…";
  if (tool.name === "create_chart") return "Drawing a chart…";
  if (tool.name === "profile_column") return "Looking at the data…";
  return "Analyzing the data…";
}

export function TurnView({ turn }: { turn: TurnState }) {
  const streaming = turn.status === "streaming";

  const parts = useMemo(() => {
    const charts = new Map<number, ChartUI>();
    let order = 0;
    for (const b of turn.blocks) {
      if (b.kind === "tool" && b.name === "create_chart" && b.status === "done" && b.ui) {
        order += 1;
        const ui = b.ui as unknown as ChartUI & { chart_id?: number };
        charts.set(ui.chart_id ?? order, ui);
      }
    }
    // Text written alongside tool calls is the agent narrating its work; the answer is the rest.
    const narration = toolSteps(turn);
    const texts = turn.blocks.filter((b): b is Extract<Block, { kind: "text" }> => b.kind === "text");
    let answer = texts.filter((b) => !narration.has(b.step)).map((b) => b.text).join("\n\n");
    // A run that stopped before its final step still shows whatever it managed to say.
    if (!answer.trim() && !streaming) answer = texts.map((b) => b.text).join("\n\n");
    return layoutAnswer(answer, charts, streaming);
  }, [turn, streaming]);

  const lastTool = [...turn.blocks].reverse().find((b): b is ToolBlock => b.kind === "tool");
  const answering = parts.some((p) => p.kind === "text");

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-3 px-4 py-2.5 text-[15px] whitespace-pre-wrap text-ink">
          {turn.question}
        </div>
      </div>

      <div className="space-y-4">
        {parts.map((p, i) =>
          p.kind === "chart" ? (
            <ChartCard key={`chart-${p.id}`} ui={p.ui} />
          ) : (
            <div key={i} className={clsx(streaming && i === parts.length - 1 && "streaming-text")}>
              <MarkdownText text={p.text} />
            </div>
          ),
        )}
        {streaming && !answering && (
          <div className="flex items-center gap-2 px-1 text-sm text-muted">
            <Spinner className="size-4" />
            {activity(lastTool)}
          </div>
        )}
        {turn.status === "stopped" && !answering && <p className="px-1 text-sm text-muted">Stopped.</p>}
        {turn.errors.map((e, i) => (
          <div
            key={i}
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-critical/40 bg-critical/10 px-3.5 py-2.5 text-sm text-ink"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-critical" />
            <span>{e}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
