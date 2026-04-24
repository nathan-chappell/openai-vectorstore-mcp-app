# File Desk UI

This subproject contains the companion React web app for the file desk. The npm workspace entrypoint now lives at the repo root.

## Commands

- `npm install`
- `npm run dev`
- `npm run build:watch`
- `npm run typecheck`
- `npm run build`

## Scope

- Clerk-authenticated file explorer
- File upload, delete, and detail views backed by `/api`
- ChatKit client that talks to `/api/chatkit`
- A secondary/demo surface that mirrors the same file-library domain used by the MCP app

## Local Development

1. Run `npm install` from the repo root.
2. Run `npm run build:watch` from the repo root.
3. Start the backend HTTP app from the repo root with `./.venv/bin/openai-vectorstore-mcp-http`.
4. Open `http://localhost:8000/`.

The built assets are served by the backend FastAPI app from the configured static directory.
