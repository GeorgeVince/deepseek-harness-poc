import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import "./styles.css";

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function* chatEvents(sessionId, message) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error("Chat stream is unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.replaceAll("\r\n", "\n").split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      let type = "message";
      const lines = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
      }
      if (lines.length) yield { type, data: JSON.parse(lines.join("\n")) };
    }
    if (done) break;
  }
}

const textOf = (message) =>
  message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("")
    .trim();

const parseArgs = (value) => {
  if (value && typeof value === "object") return value;
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" ? parsed : { value: parsed };
  } catch {
    return { value: String(value ?? "") };
  }
};

const formatValue = (value) => {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
};

function ToolCall({ toolName, args, result, isError }) {
  const name = toolName.replace("mcp__python__", "");
  return (
    <details className={`tool-call${isError ? " tool-error" : ""}`} open={result === undefined}>
      <summary><span className="trace-label">Decision trace</span> {result === undefined ? "Running" : isError ? "Failed" : "Completed"} {name}</summary>
      <pre>{formatValue(args)}</pre>
      {result !== undefined && <pre className="tool-result">{formatValue(result)}</pre>}
    </details>
  );
}

const TextPart = () => <MessagePartPrimitive.Text className="message-text" />;
const messageParts = { Text: TextPart, Empty: () => <span className="thinking">Thinking…</span>, tools: { Fallback: ToolCall } };

function UserMessage() {
  return <MessagePrimitive.Root className="user-message"><MessagePrimitive.Parts components={messageParts} /></MessagePrimitive.Root>;
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="assistant-message">
      <span className="assistant-avatar" aria-hidden="true">A</span>
      <div className="assistant-content"><MessagePrimitive.Parts components={messageParts} /></div>
    </MessagePrimitive.Root>
  );
}

function ChatThread() {
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="thread-viewport">
        <ThreadPrimitive.Empty><div className="welcome">How can I help?</div></ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        <div className="thread-spacer" />
        <div className="composer-wrap">
          <ThreadPrimitive.ScrollToBottom className="scroll-bottom" aria-label="Scroll to bottom">↓</ThreadPrimitive.ScrollToBottom>
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input rows={1} autoFocus aria-label="Message" placeholder="Type a message..." />
            <ComposerPrimitive.Send aria-label="Send message">Send</ComposerPrimitive.Send>
          </ComposerPrimitive.Root>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

function ChatPane({ session, initialMessages, onSessionsChanged }) {
  const [messages, setMessages] = useState(() =>
    initialMessages.map((message) => ({
      id: `db-${message.id}`,
      role: message.role,
      content: [
        ...(message.tool_calls || []).map((call) => ({
          type: "tool-call",
          toolCallId: call.id,
          toolName: call.name,
          args: parseArgs(call.arguments),
          argsText: call.arguments || "{}",
          ...(call.result !== null && { result: call.result, isError: call.is_error }),
        })),
        { type: "text", text: message.content },
      ],
      createdAt: new Date(message.created_at),
      ...(message.role === "assistant" && { status: { type: "complete", reason: "unknown" } }),
    })),
  );
  const [running, setRunning] = useState(false);

  const sendMessage = useCallback(async (submitted) => {
    const text = textOf(submitted);
    if (!text || running) return;

    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    setMessages((current) => [
      ...current,
      { id: userId, role: "user", content: [{ type: "text", text }] },
      { id: assistantId, role: "assistant", content: [], status: { type: "running" } },
    ]);
    setRunning(true);

    const updateAssistant = (update) => setMessages((current) =>
      current.map((message) => message.id === assistantId ? update(message) : message),
    );
    let answered = false;
    let finished = false;

    try {
      for await (const event of chatEvents(session.id, text)) {
        if (event.type === "tool_call") {
          updateAssistant((message) => ({ ...message, content: [...message.content, {
            type: "tool-call",
            toolCallId: event.data.id,
            toolName: event.data.name,
            args: parseArgs(event.data.arguments),
            argsText: typeof event.data.arguments === "string" ? event.data.arguments : JSON.stringify(event.data.arguments),
          }] }));
        }
        if (event.type === "tool_result") {
          updateAssistant((message) => ({ ...message, content: message.content.map((part) =>
            part.type === "tool-call" && part.toolCallId === event.data.id
              ? { ...part, result: event.data.result, isError: event.data.is_error }
              : part,
          ) }));
        }
        if (event.type === "assistant") {
          answered = true;
          updateAssistant((message) => ({ ...message, content: [...message.content, { type: "text", text: event.data.text }] }));
        }
        if (event.type === "done") {
          finished = true;
          updateAssistant((message) => ({
            ...message,
            content: answered ? message.content : [...message.content, { type: "text", text: event.data.response }],
            status: { type: "complete", reason: "stop" },
          }));
        }
        if (event.type === "error") throw new Error(event.data.error);
      }
      if (!finished) throw new Error("Chat stream closed unexpectedly");
      await onSessionsChanged();
    } catch (error) {
      updateAssistant((message) => ({
        ...message,
        content: [...message.content, { type: "text", text: `Error: ${error.message}` }],
        status: { type: "incomplete", reason: "error", error },
      }));
    } finally {
      setRunning(false);
    }
  }, [onSessionsChanged, running, session.id]);

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: running,
    convertMessage: (message) => message,
    onNew: sendMessage,
  });

  return <AssistantRuntimeProvider runtime={runtime}><ChatThread /></AssistantRuntimeProvider>;
}

function App() {
  const [sessions, setSessions] = useState([]);
  const [currentId, setCurrentId] = useState(localStorage.getItem("session_id"));
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshSessions = useCallback(async () => {
    const data = await api("/api/sessions");
    setSessions(data.sessions);
  }, []);

  const openSession = useCallback(async (id) => {
    setBusy(true);
    setError("");
    try {
      const data = await api(`/api/sessions/${id}/messages`);
      localStorage.setItem("session_id", id);
      setCurrentId(id);
      setMessages(data.messages);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
      setLoading(false);
    }
  }, []);

  const createSession = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const session = await api("/api/sessions", { method: "POST" });
      setSessions((current) => [session, ...current]);
      localStorage.setItem("session_id", session.id);
      setCurrentId(session.id);
      setMessages([]);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
      setLoading(false);
    }
  }, [busy]);

  useEffect(() => {
    (async () => {
      try {
        const data = await api("/api/sessions");
        setSessions(data.sessions);
        const selected = data.sessions.find((item) => item.id === currentId) || data.sessions[0];
        if (selected) await openSession(selected.id);
        else await createSession();
      } catch (caught) {
        setError(caught.message);
        setLoading(false);
      }
    })();
  }, []); // ponytail: load once; session changes are explicit user actions.

  const current = sessions.find((session) => session.id === currentId);
  return (
    <div className="app-shell">
      <aside>
        <h1>Harness Chat</h1>
        <button className="new-chat" type="button" disabled={busy} onClick={createSession}>+ New chat</button>
        <nav aria-label="Chat sessions">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={session.id === currentId ? "active" : ""}
              aria-current={session.id === currentId ? "page" : undefined}
              title={session.title}
              disabled={busy}
              onClick={() => openSession(session.id)}
            >{session.title}</button>
          ))}
        </nav>
      </aside>
      <main>
        <header>
          <h2>{current?.title || "New chat"}</h2>
          <small>Python · PostgreSQL · FastMCP · assistant-ui</small>
        </header>
        <section className="chat">
          {error ? <p className="load-state">Failed to load: {error}</p> : loading ? <p className="load-state">Loading…</p> : current && (
            <ChatPane key={current.id} session={current} initialMessages={messages} onSessionsChanged={refreshSessions} />
          )}
        </section>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
