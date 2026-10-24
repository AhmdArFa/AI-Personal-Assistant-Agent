# Web frontend (work package W7 baseline)

Minimal React + TypeScript client for the assistant API: chat with
correct Arabic, English, and mixed-direction rendering, a task
panel with live status, and an approval screen that shows the
exact tool, the exact arguments, and the arguments hash the
approval is bound to.

## Run

```bash
# backend (from the repo root)
uvicorn personal_assistant_agent.api:create_app --factory --port 8000
# (wire a checkpoint store via your own factory wrapper as needed)

# frontend
cd web
npm install
npm run dev        # Vite dev server, proxies /api to localhost:8000
npm run build      # typecheck plus production build
```

The client uses header mode (X-User-Id, X-Workspace-Id) for local
development; platform mode Bearer tokens slot in behind decision
D1 without changing the components.
