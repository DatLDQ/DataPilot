import clsx from "clsx";
import { AlertTriangle, Brain, ChevronRight, Clock, Coins, Cpu, Layers } from "lucide-react";
import { memo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatCost, formatMs, formatTokens } from "../lib/format";
import { toolSteps, type Block, type TurnState } from "../state/runReducer";
import { ToolCard } from "./ToolCard";
import { Spinner } from "./ui";

const MarkdownText = memo(function MarkdownText({ text, narration }: { text: string; narration?: boolean }) {
  return (
    <div className={clsx("md", narration && "narration")}>
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
    </div>
  );
});

function ThinkingBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const preview = text.trim().split("\n")[0]?.slice(0, 140);
  return (
    <div className="rounded-xl border border-line/70 bg-surface/50">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left"
        aria-expanded={open}
      >
        <ChevronRight className={clsx("size-4 shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <Brain className={clsx("size-4 shrink-0", live ? "animate-pulse text-accent" : "text-muted")} />
        <span className="shrink-0 text-sm text-ink-2">{live ? "Thinking" : "Thought process"}</span>
        {!open && preview && <span className="min-w-0 flex-1 truncate text-xs text-muted italic">{preview}</span>}
      </button>
      {open && (
        <div className="border-t border-line/70 px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-muted">
          {text || "…"}
        </div>
      )}
    </div>
  );
}

function StatsFooter({ turn }: { turn: TurnState }) {
  const s = turn.stats;
  if (!s) return null;
  const u = s.usage;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted">
      {turn.model && (
        <span className="flex items-center gap-1">
          <Cpu className="size-3.5" /> {turn.model}
        </span>
      )}
      <span className="flex items-center gap-1">
        <Layers className="size-3.5" /> {s.steps} {s.steps === 1 ? "step" : "steps"}
      </span>
      {s.elapsedMs > 0 && (
        <span className="flex items-center gap-1">
          <Clock className="size-3.5" /> {formatMs(s.elapsedMs)}
        </span>
      )}
      {u && (
        <span className="tabular" title="input / output / cache-read tokens">
          {formatTokens(u.input_tokens)} in · {formatTokens(u.output_tokens)} out
          {u.cache_read_input_tokens > 0 && ` · ${formatTokens(u.cache_read_input_tokens)} cached`}
        </span>
      )}
      {u && (
        <span className="flex items-center gap-1" title="Estimated cost">
          <Coins className="size-3.5" /> {formatCost(u.cost_usd)}
        </span>
      )}
    </div>
  );
}

function BlockView({ block, turn, isLast, narrationSteps }: {
  block: Block;
  turn: TurnState;
  isLast: boolean;
  narrationSteps: Set<number>;
}) {
  const live = turn.status === "streaming" && isLast;
  if (block.kind === "thinking") return <ThinkingBlock text={block.text} live={live} />;
  if (block.kind === "tool") return <ToolCard block={block} />;
  if (!block.text.trim()) return live ? <span className="caret" /> : null;
  return (
    <div className={clsx(live && "streaming-text")}>
      <MarkdownText text={block.text} narration={narrationSteps.has(block.step)} />
    </div>
  );
}

export function TurnView({ turn }: { turn: TurnState }) {
  const narrationSteps = toolSteps(turn);
  const streaming = turn.status === "streaming";
  const lastBlock = turn.blocks[turn.blocks.length - 1];
  const waiting = streaming && (!lastBlock || (lastBlock.kind === "tool" && lastBlock.status !== "drafting"));

  return (
    <section className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-3 px-4 py-2.5 text-[15px] whitespace-pre-wrap text-ink">
          {turn.question}
        </div>
      </div>

      <div className="space-y-2.5">
        {turn.blocks.map((b, i) => (
          <BlockView
            key={b.key}
            block={b}
            turn={turn}
            isLast={i === turn.blocks.length - 1}
            narrationSteps={narrationSteps}
          />
        ))}
        {waiting && (
          <div className="flex items-center gap-2 px-1 text-sm text-muted">
            <Spinner className="size-4" />
            {lastBlock ? "Reading results…" : "Starting analysis…"}
          </div>
        )}
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
        {!streaming && <StatsFooter turn={turn} />}
      </div>
    </section>
  );
}
