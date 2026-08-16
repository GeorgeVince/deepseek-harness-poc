import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessagePartReasoning,
} from "@assistant-ui/react";
import "./styles.css";

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || (typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg) || `HTTP ${response.status}`);
  return data;
}

async function* chatEvents(sessionId, message) {
  const response = await fetch(`/api/chats/${sessionId}/stream`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
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

const withProcess = (parts) => {
  const steps = [...new Set(parts
    .filter((part) => part.type === "tool-call" && part.reasoning && !part.isError)
    .map((part) => part.reasoning.replace(/\s+/g, " ").trim()))];
  const content = parts.filter((part) => part.type !== "reasoning");
  return steps.length ? [{ type: "reasoning", text: steps.join("\n"), status: { type: "running" } }, ...content] : content;
};

function ToolCall({ result, isError }) {
  if (result === undefined) return null;
  if (isError) return (
    <details className="adjusted-attempt">
      <summary><span aria-hidden="true" />Adjusted approach</summary>
      <pre>{formatValue(result)}</pre>
    </details>
  );
  const resultData = parseArgs(result);
  const artifacts = Array.isArray(resultData.artifacts) ? resultData.artifacts : [];
  if (artifacts.length === 0) return null;
  return (
    <div className="workbook-output">
      <div className="workbook-heading"><span aria-hidden="true" />Workbook ready</div>
      <div className="tool-artifacts">
        {artifacts.map((artifact) => (
          <a key={artifact.name} href={`/api/files/${encodeURIComponent(artifact.name)}`} download>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.75h6.5L18 8.25v12H7zM13.5 3.75v4.5H18M9.5 16.25h6M9.5 12.75h6" /></svg>
            <span><strong>{artifact.name}</strong><small>{artifact.change === "created" ? "Created" : "Updated"} · {Math.ceil(artifact.size / 1024)} KB</small></span>
            <svg className="download-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10m0 0 4-4m-4 4-4-4M5 19.5h14" /></svg>
          </a>
        ))}
      </div>
    </div>
  );
}

const TextPart = () => <MessagePartPrimitive.Text className="message-text" />;
const ProcessPart = () => {
  const { text, status } = useMessagePartReasoning();
  const steps = text.split("\n").filter(Boolean);
  const icon = <span className="process-icon" aria-hidden="true"><i /><i /><i /></span>;
  if (status.type === "running") return (
    <div className="process-live">
      {icon}
      <div><span className="process-label">Working</span>{steps.at(-1)}</div>
    </div>
  );
  return (
    <details className="process-history">
      <summary>{icon}<span>Process</span><small>{steps.length} {steps.length === 1 ? "step" : "steps"}</small></summary>
      <ol>{steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>
    </details>
  );
};
const messageParts = { Text: TextPart, Reasoning: ProcessPart, Empty: () => <span className="thinking">Thinking…</span>, tools: { Fallback: ToolCall } };

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

function ChatPane({ session, initialMessages, onSessionsChanged, onFilesChanged }) {
  const [messages, setMessages] = useState(() =>
    initialMessages.map((message) => ({
      id: `db-${message.id}`,
      role: message.role,
      content: withProcess([
        ...(message.tool_calls || []).map((call) => ({
          type: "tool-call",
          toolCallId: call.id,
          toolName: call.name,
          args: parseArgs(call.arguments),
          argsText: call.arguments || "{}",
          reasoning: call.reasoning,
          ...(call.result !== null && { result: call.result, isError: call.is_error }),
        })),
        { type: "text", text: message.content },
      ]),
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
          updateAssistant((message) => ({ ...message, content: withProcess([
            ...message.content,
            {
              type: "tool-call",
              toolCallId: event.data.id,
              toolName: event.data.name,
              args: parseArgs(event.data.arguments),
              argsText: typeof event.data.arguments === "string" ? event.data.arguments : JSON.stringify(event.data.arguments),
              reasoning: event.data.reasoning,
            },
          ]) }));
        }
        if (event.type === "tool_result") {
          updateAssistant((message) => {
            const index = message.content.findIndex((part) => part.type === "tool-call" && part.toolCallId === event.data.id);
            if (index < 0) return message;
            const tool = { ...message.content[index], result: event.data.result, isError: event.data.is_error };
            return { ...message, content: withProcess([
              ...message.content.slice(0, index),
              tool,
              ...message.content.slice(index + 1),
            ]) };
          });
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
      await Promise.all([onSessionsChanged(), onFilesChanged()]);
    } catch (error) {
      updateAssistant((message) => ({
        ...message,
        content: [...message.content, { type: "text", text: `Error: ${error.message}` }],
        status: { type: "incomplete", reason: "error", error },
      }));
    } finally {
      setRunning(false);
    }
  }, [onFilesChanged, onSessionsChanged, running, session.id]);

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: running,
    convertMessage: (message) => message,
    onNew: sendMessage,
  });

  return <AssistantRuntimeProvider runtime={runtime}><ChatThread /></AssistantRuntimeProvider>;
}

function WorkspaceFiles({ files, onUpload, uploading, error }) {
  return (
    <div className="workspace-files">
      <label className="upload-button">
        {uploading ? "Uploading…" : "+ Excel"}
        <input
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) onUpload(file);
          }}
        />
      </label>
      {error && <span className="file-error" title={error}>Upload failed</span>}
      <div className="file-links">
        {files.length === 0 ? <small>No workbooks</small> : files.map((file) => (
          <a key={file.name} href={`/api/files/${encodeURIComponent(file.name)}`} download title={`${file.name} · ${Math.ceil(file.size / 1024)} KB`}>
            {file.name}
          </a>
        ))}
      </div>
    </div>
  );
}

function App() {
  const [sessions, setSessions] = useState([]);
  const [files, setFiles] = useState([]);
  const [currentId, setCurrentId] = useState(localStorage.getItem("session_id"));
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");

  const refreshSessions = useCallback(async () => {
    const data = await api("/api/chats");
    setSessions(data.chats);
  }, []);

  const refreshFiles = useCallback(async () => {
    const data = await api("/api/files");
    setFiles(data.files);
  }, []);

  const uploadWorkbook = useCallback(async (file) => {
    setFileError("");
    if (!/^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.xlsx$/i.test(file.name) || file.size === 0 || file.size > 64 * 1024 * 1024) {
      setFileError("Choose an XLSX file up to 64 MB with a simple filename.");
      return;
    }
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      let { upload } = await api("/api/files", { method: "POST", body: form });
      for (let attempt = 0; upload.status === "processing" && attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        ({ upload } = await api(`/api/uploads/${upload.id}`));
      }
      if (upload.status !== "complete") throw new Error(upload.error || "Upload timed out");
      await refreshFiles();
    } catch (caught) {
      setFileError(caught.message);
    } finally {
      setUploading(false);
    }
  }, [refreshFiles]);

  const openSession = useCallback(async (id) => {
    setBusy(true);
    setError("");
    try {
      const data = await api(`/api/chats/${id}/history`);
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
      const session = await api("/api/chats", { method: "POST" });
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
        const [data, fileData] = await Promise.all([api("/api/chats"), api("/api/files")]);
        setSessions(data.chats);
        setFiles(fileData.files);
        const selected = data.chats.find((item) => item.id === currentId) || data.chats[0];
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
          <div className="header-title">
            <h2>{current?.title || "New chat"}</h2>
            <small>Python · PostgreSQL · FastMCP · assistant-ui</small>
          </div>
          <WorkspaceFiles files={files} onUpload={uploadWorkbook} uploading={uploading} error={fileError} />
        </header>
        <section className="chat">
          {error ? <p className="load-state">Failed to load: {error}</p> : loading ? <p className="load-state">Loading…</p> : current && (
            <ChatPane key={current.id} session={current} initialMessages={messages} onSessionsChanged={refreshSessions} onFilesChanged={refreshFiles} />
          )}
        </section>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
