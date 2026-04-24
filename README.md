# OpenAI File Desk

This repo contains a Clerk-authenticated file desk built around one shared domain layer with two delivery surfaces:

- an MCP server with interactive files, search, and branch-search UIs
- a companion React web app that mirrors file browsing, upload, delete, and ChatKit chat

## Architecture

- Shared backend domain: [`backend`](backend)
- Companion web app: [`ui`](ui)
- Integration tests: [`tests/integration/test_openai_vectorstore_mcp_app.py`](tests/integration/test_openai_vectorstore_mcp_app.py)

The backend is intentionally split into:

- `bootstrap.py` for shared service wiring
- `mcp_app.py` for MCP auth, tools, and the Prefab files/search UIs
- `web_app.py` for FastAPI routes, static hosting, `/api/chatkit`, and mounting `/mcp`

## Tool Surface

The MCP app is the primary product surface. It exposes:

- `list_files`
- `list_tags`
- `search_files`
- `search_file_branches`
- `get_file_detail`
- `read_file_text`
- `delete_file`
- `files`
- `file_search`
- `branch_search`

The web app is a companion/demo surface. It uses the same `FileLibraryService` for `/api/files`, `/api/tags`, upload session + upload finalize, file downloads, and ChatKit.

## Local Development

1. Create `.env` values from [`.env.example`](.env.example).
2. Install Python dependencies into the repo `.venv`.
3. Run `npm install`.
4. Run `npm run build:watch`.
5. Start the HTTP app with `./.venv/bin/openai-vectorstore-mcp-http`.
6. Open `http://localhost:8000/` for the companion web app.
7. Point an MCP-compatible host at `http://localhost:8000/mcp`.

Set `CLERK_PUBLISHABLE_KEY` when you want local Clerk sign-in in the web app.

## Verification

- `./.venv/bin/pytest tests/integration/test_openai_vectorstore_mcp_app.py`
- `npm run typecheck`
- `npm run build`
