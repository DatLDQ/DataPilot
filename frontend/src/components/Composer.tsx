import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface Props {
  running: boolean;
  disabled?: boolean;
  onSend: (question: string) => void;
  onStop: () => void;
}

export function Composer({ running, disabled, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    const q = value.trim();
    if (!q || running || disabled) return;
    onSend(q);
    setValue("");
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="flex items-end gap-2 rounded-2xl border border-line-strong bg-surface-2 p-2 pl-4 shadow-lg focus-within:border-accent/60">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? "Select or upload a dataset to start" : "Ask a question about your data…"}
          aria-label="Question"
          className="max-h-[200px] min-h-[24px] flex-1 resize-none bg-transparent py-1.5 text-[15px] text-ink outline-none placeholder:text-muted"
        />
        {running ? (
          <button
            type="button"
            onClick={onStop}
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-surface-3 text-ink hover:bg-line-strong"
            title="Stop"
            aria-label="Stop"
          >
            <Square className="size-3.5 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || disabled}
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent-strong text-white hover:bg-accent disabled:bg-surface-3 disabled:text-muted"
            title="Send"
            aria-label="Send"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
      <p className="mt-1.5 text-center text-[11px] text-muted">
        Enter to send · Shift+Enter for a new line
      </p>
    </div>
  );
}
