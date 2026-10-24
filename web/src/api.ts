// API client for the assistant backend (header mode for local
// development; platform mode tokens slot in behind D1).

export interface Session {
  userId: string;
  workspaceId: string;
}

export interface ChatReply {
  conversation_id: string;
  reply: string;
  provider: string;
  model_version: string;
  tokens_used: number;
  cost_usd: number;
}

export interface PendingApproval {
  approval_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  arguments_hash: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  current_step: string | null;
  completed_steps: string[];
  steps_executed: number;
  error_summary: string;
  pending_approval: PendingApproval | null;
}

export interface TaskResult {
  task_id: string;
  status: string;
  stop_reason: string | null;
  answer: string | null;
  summary: string;
  error_summary: string;
}

function headers(session: Session): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-User-Id": session.userId,
    "X-Workspace-Id": session.workspaceId,
  };
}

export async function sendChat(
  session: Session,
  message: string,
  conversationId: string | null,
): Promise<ChatReply> {
  const response = await fetch("/api/v1/chat", {
    method: "POST",
    headers: headers(session),
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (!response.ok) throw new Error(`chat failed: ${response.status}`);
  return (await response.json()) as ChatReply;
}

export async function createTask(
  session: Session,
  request: string,
): Promise<TaskResult> {
  const response = await fetch("/api/v1/tasks", {
    method: "POST",
    headers: headers(session),
    body: JSON.stringify({ request }),
  });
  if (!response.ok) throw new Error(`create task failed: ${response.status}`);
  return (await response.json()) as TaskResult;
}

export async function getTaskStatus(
  session: Session,
  taskId: string,
): Promise<TaskStatus> {
  const response = await fetch(`/api/v1/tasks/${taskId}`, {
    headers: headers(session),
  });
  if (!response.ok) throw new Error(`status failed: ${response.status}`);
  return (await response.json()) as TaskStatus;
}

export async function decideApproval(
  session: Session,
  taskId: string,
  approvalId: string,
  approved: boolean,
): Promise<TaskResult> {
  const response = await fetch(`/api/v1/tasks/${taskId}/approve`, {
    method: "POST",
    headers: headers(session),
    body: JSON.stringify({ approved, approval_id: approvalId }),
  });
  if (!response.ok) throw new Error(`approve failed: ${response.status}`);
  return (await response.json()) as TaskResult;
}

export async function cancelTask(
  session: Session,
  taskId: string,
): Promise<void> {
  await fetch(`/api/v1/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: headers(session),
  });
}
