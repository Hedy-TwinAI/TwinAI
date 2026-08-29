import { useEffect, useRef, useState } from "react";
import "./AssistantChat.css";
import { askAssistant } from "../api";

export default function AssistantChat({ context }) {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  async function handleSubmit(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || isLoading) return;

    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setQuestion("");
    setIsLoading(true);
    setError(null);

    try {
      const { answer } = await askAssistant(q, context);
      setMessages((prev) => [...prev, { role: "assistant", content: answer }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="assistant-chat">
      <div className="assistant-chat__header">
        <h3 className="assistant-chat__title">Ask about this run</h3>
        {isLoading && <span className="assistant-chat__status">thinking…</span>}
      </div>

      <div ref={scrollRef} className="assistant-chat__messages">
        {messages.length === 0 && (
          <p className="assistant-chat__empty">
            Ask a question about the current simulation run — e.g. "Why is the queue so long
            around minute 200?"
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`assistant-chat__message ${
              m.role === "user" ? "assistant-chat__message--user" : "assistant-chat__message--assistant"
            }`}
          >
            {m.content}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="assistant-chat__form">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question…"
          className="assistant-chat__input"
        />
        <button type="submit" disabled={isLoading || !question.trim()} className="assistant-chat__send-btn">
          Ask
        </button>
      </form>

      {error && <div className="assistant-chat__error">{error}</div>}
    </div>
  );
}
