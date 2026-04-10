# OpenAI Files Vector Store UI

This subproject contains the phase-1 MCP App UI for the `openai-files-vector-store` server.

## Commands

- `npm install`
- `npm run dev`
- `npm run dev:mock`
- `npm run build:watch`
- `npm run host:build`
- `npm run typecheck`
- `npm run build`

## Development modes

- `npm run dev` starts the real local MCP App loop:
  - the UI single-file build watcher for `dist/mcp-app.html`
  - a repo-local test host adapted from `modelcontextprotocol/ext-apps/examples/basic-host`
  - the Python FastMCP server over streamable HTTP at `http://127.0.0.1:8000/mcp`
  - the host UI at `http://127.0.0.1:8080`
- `npm run dev:mock` keeps the old standalone Vite mode with mock vector-store data on `http://localhost:5174/`.
- `npm run build:watch` keeps `dist/mcp-app.html` fresh for VS Code and the Python MCP server.
- `npm run host:build` builds the local test-host assets into `host-dist/` without starting any long-running processes.

## Current scope

- Browse recent vector stores
- Inspect one store's status and attached files
- Run direct vector store search
- Run `ask_vector_store` grounded retrieval

Phase 1 intentionally excludes browser-side uploads and file attachment flows.
