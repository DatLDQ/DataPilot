import { AlertTriangle, FlaskConical, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { api, ApiError, askQuestion } from "./api/client";
import type {
  Conversation,
  DatasetDetail,
  DatasetSummary,
  Health,
} from "./api/types";
import { Composer } from "./components/Composer";
import { ACCEPT, Sidebar } from "./components/Sidebar";
import { TurnView } from "./components/TurnView";
import { NoDataset, Suggestions } from "./components/Welcome";
import {
  newTurn,
  reduceTurn,
  replayTurn,
  type TurnState,
} from "./state/runReducer";

const LAST_DATASET = "datapilot:last-dataset";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnState[]>([]);
  const [running, setRunning] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const datasetId = dataset?.id ?? null;

  const fail = useCallback((e: unknown) => setToast(message(e)), []);

  const stopRun = useCallback(() => {
    runIdRef.current++;
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  // ---- loading ------------------------------------------------------------
  const selectDataset = useCallback(
    async (id: string) => {
      stopRun();
      setConversationId(null);
      setTurns([]);
      setSuggestions(null);
      try {
        const [detail, convs] = await Promise.all([
          api.getDataset(id),
          api.listConversations(id),
        ]);
        setDataset(detail);
        setConversations(convs);
        localStorage.setItem(LAST_DATASET, id);
      } catch (e) {
        fail(e);
        return;
      }
      api
        .suggestions(id)
        .then((r) => setSuggestions(r.suggestions))
        .catch(() => setSuggestions([]));
    },
    [fail, stopRun],
  );

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
    api
      .listDatasets()
      .then((list) => {
        setDatasets(list);
        const last = localStorage.getItem(LAST_DATASET);
        const pick = list.find((d) => d.id === last) ?? list[0];
        if (pick) void selectDataset(pick.id);
      })
      .catch(fail);
  }, [fail, selectDataset]);

  const refreshConversations = useCallback(async (dsId: string) => {
    try {
      setConversations(await api.listConversations(dsId));
    } catch {
      /* non-critical */
    }
  }, []);

  const selectConversation = async (id: string) => {
    stopRun();
    setConversationId(id);
    stickToBottom.current = true;
    try {
      const detail = await api.getConversation(id);
      setTurns(detail.turns.map((t) => replayTurn(t.question, t.events)));
    } catch (e) {
      fail(e);
    }
  };

  const newConversation = () => {
    stopRun();
    setConversationId(null);
    setTurns([]);
  };

  // ---- dataset actions ----------------------------------------------------
  const afterCreate = async (ds: DatasetSummary) => {
    setDatasets((prev) => [ds, ...prev.filter((d) => d.id !== ds.id)]);
    await selectDataset(ds.id);
  };

  const upload = async (file: File) => {
    setUploading(true);
    try {
      await afterCreate(await api.uploadDataset(file));
    } catch (e) {
      fail(e);
    } finally {
      setUploading(false);
    }
  };

  const deleteDataset = async (id: string) => {
    try {
      await api.deleteDataset(id);
      const rest = datasets.filter((d) => d.id !== id);
      setDatasets(rest);
      if (id === datasetId) {
        stopRun();
        setDataset(null);
        setConversations([]);
        setConversationId(null);
        setTurns([]);
        if (rest[0]) void selectDataset(rest[0].id);
      }
    } catch (e) {
      fail(e);
    }
  };

  const deleteConversation = async (id: string) => {
    try {
      await api.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (id === conversationId) newConversation();
    } catch (e) {
      fail(e);
    }
  };

  // ---- asking -------------------------------------------------------------
  const ask = async (question: string) => {
    if (!dataset || running) return;
    const runId = ++runIdRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    stickToBottom.current = true;
    setTurns((prev) => [...prev, newTurn(question)]);

    // Ignore updates once the user has switched away (stopRun bumps the run id).
    const patchLast = (fn: (t: TurnState) => TurnState) => {
      if (runIdRef.current !== runId) return;
      setTurns((prev) =>
        prev.length ? [...prev.slice(0, -1), fn(prev[prev.length - 1])] : prev,
      );
    };

    try {
      let convId = conversationId;
      if (!convId) {
        const conv = await api.createConversation(dataset.id);
        convId = conv.id;
        setConversationId(conv.id);
        setConversations((prev) => [conv, ...prev]);
      }
      for await (const ev of askQuestion(convId, question, controller.signal)) {
        if (runIdRef.current !== runId) break;
        patchLast((t) => reduceTurn(t, ev));
      }
    } catch (e) {
      if (controller.signal.aborted) {
        patchLast((t) =>
          t.status === "streaming" ? { ...t, status: "stopped" } : t,
        );
      } else {
        const msg =
          e instanceof ApiError || e instanceof Error ? e.message : String(e);
        patchLast((t) => ({
          ...t,
          status: "error",
          errors: [...t.errors, msg],
        }));
      }
    } finally {
      // A stream that closed without a `done` event (e.g. network drop) shouldn't spin forever.
      patchLast((t) =>
        t.status === "streaming"
          ? { ...t, status: "error", errors: [...t.errors, "Connection lost."] }
          : t,
      );
      // Skip both once the user switched away: a late refresh would overwrite the new dataset's list.
      if (runIdRef.current === runId) {
        abortRef.current = null;
        setRunning(false);
        void refreshConversations(dataset.id);
      }
    }
  };

  const stop = () => {
    abortRef.current?.abort();
  };

  // ---- auto-scroll --------------------------------------------------------
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const onScroll = () => {
    const el = scroller.current;
    if (el)
      stickToBottom.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  // ---- render -------------------------------------------------------------
  const activeTitle = conversations.find((c) => c.id === conversationId)?.title;

  return (
    <div className="flex h-full bg-page text-ink">
      <Sidebar
        datasets={datasets}
        conversations={conversations}
        datasetId={datasetId}
        conversationId={conversationId}
        busy={uploading}
        onSelectDataset={(id) => void selectDataset(id)}
        onSelectConversation={(id) => void selectConversation(id)}
        onNewConversation={newConversation}
        onDeleteDataset={(id) => void deleteDataset(id)}
        onDeleteConversation={(id) => void deleteConversation(id)}
        onUpload={(f) => void upload(f)}
      />

      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
          <div className="min-w-0 truncate text-sm text-ink-2">
            {dataset ? (
              <>
                <span className="text-muted">{dataset.name}</span>
                {activeTitle && <span className="text-muted"> / </span>}
                {activeTitle && <span className="text-ink">{activeTitle}</span>}
              </>
            ) : (
              "No dataset selected"
            )}
          </div>
          <div className="flex items-center gap-3">
            {health?.demo_mode && (
              <span className="rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent">
                Demo
              </span>
            )}
          </div>
        </header>

        {health?.demo_mode ? (
          <div className="flex items-center gap-2 border-b border-accent/30 bg-accent/10 px-4 py-2 text-sm text-ink-2">
            <FlaskConical className="size-4 shrink-0 text-accent" />
            <span>
              Demo mode: answers come from an offline, rule-based agent (real
              SQL and charts, no Claude, no cost). Set{" "}
              <code className="font-mono">ANTHROPIC_API_KEY</code> (or{" "}
              <code className="font-mono">GEMINI_API_KEY</code>) on the server
              for real analysis.
            </span>
          </div>
        ) : (
          health &&
          !health.api_key_configured && (
            <div className="flex items-center gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-ink-2">
              <AlertTriangle className="size-4 shrink-0 text-warning" />
              <span>
                <code className="font-mono">
                  {health.provider === "gemini" ? "GEMINI_API_KEY" : "ANTHROPIC_API_KEY"}
                </code>{" "}
                is not set on the server. You can browse data, but questions will fail
                until it is configured.
              </span>
            </div>
          )
        )}

        <div
          ref={scroller}
          onScroll={onScroll}
          className="min-h-0 flex-1 overflow-y-auto"
        >
          {!dataset ? (
            <NoDataset
              busy={uploading}
              onUpload={() => fileInput.current?.click()}
            />
          ) : turns.length === 0 ? (
            <Suggestions
              dataset={dataset}
              suggestions={suggestions}
              onPick={(q) => void ask(q)}
            />
          ) : (
            <div className="mx-auto max-w-3xl space-y-10 px-4 py-8">
              {turns.map((t, i) => (
                <TurnView key={i} turn={t} />
              ))}
            </div>
          )}
        </div>

        <Composer
          running={running}
          disabled={!dataset}
          onSend={(q) => void ask(q)}
          onStop={stop}
        />

        {toast && (
          <div
            role="alert"
            className="absolute top-14 left-1/2 z-40 flex max-w-lg -translate-x-1/2 items-start gap-2 rounded-lg border border-critical/40 bg-surface-2 px-3 py-2 text-sm text-ink shadow-xl"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-critical" />
            <span className="min-w-0 flex-1">{toast}</span>
            <button
              type="button"
              onClick={() => setToast(null)}
              className="text-muted hover:text-ink"
              aria-label="Dismiss"
            >
              <X className="size-4" />
            </button>
          </div>
        )}
      </main>

      <input
        ref={fileInput}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
