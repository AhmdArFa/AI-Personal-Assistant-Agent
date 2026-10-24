import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Session,
  TaskStatus,
  cancelTask,
  createTask,
  decideApproval,
  getTaskStatus,
  sendChat,
} from "./api";

// Render text with fenced code blocks. Every block uses dir="auto"
// (and plaintext bidi in CSS) so Arabic, English, and mixed text
// each take their correct direction per block.
function RichText({ text }: { text: string }) {
  const parts = text.split(/```/);
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <pre key={index} dir="ltr" className="code">
            {part}
          </pre>
        ) : (
          <span key={index} dir="auto" className="bidi">
            {part}
          </span>
        ),
      )}
    </>
  );
}

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

export default function App() {
  const [session, setSession] = useState<Session>({
    userId: localStorage.getItem("userId") ?? "user-1",
    workspaceId: localStorage.getItem("workspaceId") ?? "ws-1",
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [taskRequest, setTaskRequest] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);
  const [notice, setNotice] = useState("");
  const pollRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    localStorage.setItem("userId", session.userId);
    localStorage.setItem("workspaceId", session.workspaceId);
  }, [session]);

  useEffect(() => {
    if (!taskId) return;
    const poll = async () => {
      try {
        setTaskStatus(await getTaskStatus(session, taskId));
      } catch {
        setNotice("Could not load task status.");
      }
    };
    void poll();
    pollRef.current = window.setInterval(poll, 2000);
    return () => window.clearInterval(pollRef.current);
  }, [taskId, session]);

  async function onChatSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    try {
      const reply = await sendChat(session, text, conversationId);
      setConversationId(reply.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", text: reply.reply }]);
    } catch {
      setNotice("Chat request failed. Is the API running on port 8000?");
    }
  }

  async function onTaskSubmit(event: FormEvent) {
    event.preventDefault();
    const request = taskRequest.trim();
    if (!request) return;
    setTaskRequest("");
    try {
      const result = await createTask(session, request);
      setTaskId(result.task_id);
      setNotice(result.summary);
    } catch {
      setNotice("Task creation failed.");
    }
  }

  async function onApproval(approved: boolean) {
    if (!taskId || !taskStatus?.pending_approval) return;
    try {
      const result = await decideApproval(
        session,
        taskId,
        taskStatus.pending_approval.approval_id,
        approved,
      );
      setNotice(result.summary);
      setTaskStatus(await getTaskStatus(session, taskId));
    } catch {
      setNotice("Approval request failed.");
    }
  }

  const pending = taskStatus?.pending_approval ?? null;

  return (
    <main className="layout">
      <header>
        <h1>AI Personal Assistant</h1>
        <div className="session">
          <label>
            User
            <input
              value={session.userId}
              onChange={(e) => setSession({ ...session, userId: e.target.value })}
            />
          </label>
          <label>
            Workspace
            <input
              value={session.workspaceId}
              onChange={(e) =>
                setSession({ ...session, workspaceId: e.target.value })
              }
            />
          </label>
        </div>
      </header>

      {notice && <p className="notice">{notice}</p>}

      <section className="panel">
        <h2>Chat</h2>
        <div className="messages">
          {messages.map((message, index) => (
            <div key={index} className={`message ${message.role}`} dir="auto">
              <RichText text={message.text} />
            </div>
          ))}
        </div>
        <form onSubmit={onChatSubmit} className="row">
          <input
            dir="auto"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="اكتب رسالتك أو write your message"
          />
          <button type="submit">Send</button>
        </form>
      </section>

      <section className="panel">
        <h2>Tasks</h2>
        <form onSubmit={onTaskSubmit} className="row">
          <input
            dir="auto"
            value={taskRequest}
            onChange={(e) => setTaskRequest(e.target.value)}
            placeholder="Describe a task"
          />
          <button type="submit">Run task</button>
        </form>
        {taskStatus && (
          <div className="task-status">
            <p>
              Status: <strong>{taskStatus.status}</strong>
              {taskStatus.current_step &&
                ` · current step: ${taskStatus.current_step}`}
              {taskStatus.completed_steps.length > 0 &&
                ` · done: ${taskStatus.completed_steps.join(", ")}`}
            </p>
            {taskStatus.error_summary && (
              <p className="error">{taskStatus.error_summary}</p>
            )}
            {taskId && taskStatus.status === "running" && (
              <button onClick={() => void cancelTask(session, taskId)}>
                Cancel task
              </button>
            )}
          </div>
        )}

        {pending && (
          <div className="approval">
            <h3>Approval required</h3>
            <p>
              Tool: <strong>{pending.tool_name}</strong>
            </p>
            <p>The exact arguments this approval covers:</p>
            <pre dir="ltr" className="code">
              {JSON.stringify(pending.tool_args, null, 2)}
            </pre>
            <p className="hash" dir="ltr">
              arguments hash: {pending.arguments_hash}
            </p>
            <p>
              Approving runs exactly this action with exactly these
              arguments. If the arguments change, you will be asked
              again.
            </p>
            <div className="row">
              <button onClick={() => void onApproval(true)}>Approve</button>
              <button onClick={() => void onApproval(false)}>Deny</button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
