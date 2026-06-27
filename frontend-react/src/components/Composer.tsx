import { useState, type FormEvent } from "react";

export function Composer({
  placeholder,
  onSend,
  disabled,
}: {
  placeholder: string;
  onSend: (text: string) => void;
  disabled?: boolean;
}) {
  const [text, setText] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
  }
  return (
    <form
      onSubmit={submit}
      className="flex items-center gap-3 rounded-full border border-hairline bg-surface px-4 py-2.5 shadow-card"
    >
      <input
        className="flex-1 bg-transparent text-[15px] outline-none placeholder:text-text-tertiary"
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white disabled:opacity-40"
        aria-label="send"
      >
        ↑
      </button>
    </form>
  );
}
