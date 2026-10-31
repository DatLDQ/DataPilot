import clsx from "clsx";
import { FileSpreadsheet, MessageSquare, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import type { Conversation, DatasetSummary } from "../api/types";
import { Spinner } from "./ui";

export const ACCEPT = ".csv,.tsv,.txt,.parquet,.json,.jsonl,.ndjson,.xlsx,.xls";

interface Props {
  datasets: DatasetSummary[];
  conversations: Conversation[];
  datasetId: string | null;
  conversationId: string | null;
  busy: boolean;
  onSelectDataset: (id: string) => void;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteDataset: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onUpload: (file: File) => void;
}

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#222221" />
        <rect x="7" y="17" width="4" height="8" rx="1.5" fill="#3987e5" />
        <rect x="14" y="11" width="4" height="14" rx="1.5" fill="#3987e5" />
        <rect x="21" y="6" width="4" height="19" rx="1.5" fill="#199e70" />
      </svg>
      <div>
        <div className="text-[15px] leading-tight font-semibold text-ink">DataPilot</div>
        <div className="text-[11px] leading-tight text-muted">AI data analyst</div>
      </div>
    </div>
  );
}

export function Sidebar(p: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) p.onUpload(file);
  };

  return (
    <aside
      className={clsx(
        "flex h-full w-64 shrink-0 flex-col border-r border-line bg-surface transition-colors",
        dragging && "bg-accent/10",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="px-4 pt-4 pb-3">
        <Logo />
      </div>

      <div className="flex gap-2 px-3 pb-3">
        <button
          type="button"
          disabled={p.busy}
          onClick={() => fileInput.current?.click()}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent-strong px-2 py-2 text-sm font-medium text-white hover:bg-accent disabled:opacity-50"
        >
          {p.busy ? <Spinner className="size-4" /> : <Upload className="size-4" />}
          Upload
        </button>
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) p.onUpload(file);
            e.target.value = "";
          }}
        />
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        <SectionTitle>Datasets</SectionTitle>
        {p.datasets.length === 0 && (
          <p className="px-2 py-1 text-xs text-muted">Drop a CSV, Excel, Parquet or JSON file here.</p>
        )}
        {p.datasets.map((d) => (
          <Row
            key={d.id}
            active={d.id === p.datasetId}
            icon={<FileSpreadsheet className="size-4" />}
            label={d.name}
            meta={`${d.tables.reduce((s, t) => s + t.row_count, 0).toLocaleString()} rows`}
            onClick={() => p.onSelectDataset(d.id)}
            onDelete={() => p.onDeleteDataset(d.id)}
            deleteLabel={`Delete dataset ${d.name}`}
          />
        ))}

        {p.datasetId && (
          <>
            <div className="mt-4 flex items-center justify-between pr-1">
              <SectionTitle>Analyses</SectionTitle>
              <button
                type="button"
                onClick={p.onNewConversation}
                className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink"
                title="New analysis"
                aria-label="New analysis"
              >
                <Plus className="size-4" />
              </button>
            </div>
            {p.conversations.length === 0 && <p className="px-2 py-1 text-xs text-muted">No analyses yet.</p>}
            {p.conversations.map((c) => (
              <Row
                key={c.id}
                active={c.id === p.conversationId}
                icon={<MessageSquare className="size-4" />}
                label={c.title}
                onClick={() => p.onSelectConversation(c.id)}
                onDelete={() => p.onDeleteConversation(c.id)}
                deleteLabel={`Delete analysis ${c.title}`}
              />
            ))}
          </>
        )}
      </nav>
    </aside>
  );
}

function SectionTitle({ children }: { children: string }) {
  return <div className="px-2 pt-1 pb-1.5 text-[11px] font-semibold tracking-wider text-muted uppercase">{children}</div>;
}

function Row(props: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  meta?: string;
  onClick: () => void;
  onDelete: () => void;
  deleteLabel: string;
}) {
  return (
    <div
      className={clsx(
        "group flex items-center rounded-lg pr-1 text-sm",
        props.active ? "bg-surface-3 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
      )}
    >
      <button type="button" onClick={props.onClick} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left">
        <span className="shrink-0 text-muted">{props.icon}</span>
        <span className="min-w-0 flex-1 truncate">{props.label}</span>
        {props.meta && <span className="tabular shrink-0 text-[11px] text-muted">{props.meta}</span>}
      </button>
      <button
        type="button"
        onClick={() => {
          if (confirm(`${props.deleteLabel}?`)) props.onDelete();
        }}
        className="rounded p-1 text-muted opacity-0 group-hover:opacity-100 hover:text-critical focus:opacity-100"
        aria-label={props.deleteLabel}
        title="Delete"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  );
}
