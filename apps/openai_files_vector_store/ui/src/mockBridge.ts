import type { McpUiHostContext } from "@modelcontextprotocol/ext-apps";

import type {
  AskVectorStoreArguments,
  AskVectorStoreResult,
  FilePreviewResult,
  GetVectorStoreStatusArguments,
  OpenVectorStoreConsoleResult,
  PreviewFileArguments,
  SearchHit,
  SearchVectorStoreArguments,
  SearchVectorStoreResult,
  VectorStoreListResult,
  VectorStoreStatusResult,
} from "./types";
import type { VectorStoreConsoleBridge } from "./bridge";

type MockDocument = {
  vector_store_id: string;
  file_id: string;
  filename: string;
  text: string;
};

const HOST_CONTEXT: McpUiHostContext = {
  theme: "light",
  locale: "en-US",
  displayMode: "inline",
  availableDisplayModes: ["inline", "fullscreen"],
  safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
};

const VECTOR_STORE_LIST: VectorStoreListResult = {
  total_returned: 2,
  vector_stores: [
    {
      id: "vs_demo_ops",
      name: "Operations Runbooks",
      status: "completed",
      created_at: 1_744_281_600,
      last_active_at: 1_744_296_400,
      usage_bytes: 13_824,
      expires_at: null,
      metadata: { owner: "platform", dataset: "runbooks" },
      file_counts: {
        completed: 2,
        failed: 0,
        in_progress: 0,
        cancelled: 0,
        total: 2,
      },
    },
    {
      id: "vs_demo_agent",
      name: "Agent Guidance Notes",
      status: "completed",
      created_at: 1_744_288_800,
      last_active_at: 1_744_297_300,
      usage_bytes: 9_216,
      expires_at: null,
      metadata: { owner: "assistant", dataset: "guidance" },
      file_counts: {
        completed: 1,
        failed: 0,
        in_progress: 0,
        cancelled: 0,
        total: 1,
      },
    },
  ],
};

const STATUS_BY_ID: Record<string, VectorStoreStatusResult> = {
  vs_demo_ops: {
    vector_store: VECTOR_STORE_LIST.vector_stores[0],
    files: [
      {
        id: "file_ops_alpha",
        created_at: 1_744_281_620,
        status: "completed",
        usage_bytes: 6_912,
        vector_store_id: "vs_demo_ops",
        attributes: { source: "ops-alpha" },
        last_error: null,
      },
      {
        id: "file_ops_beta",
        created_at: 1_744_281_780,
        status: "completed",
        usage_bytes: 6_912,
        vector_store_id: "vs_demo_ops",
        attributes: { source: "ops-beta" },
        last_error: null,
      },
    ],
    batch: null,
    batch_files: [],
  },
  vs_demo_agent: {
    vector_store: VECTOR_STORE_LIST.vector_stores[1],
    files: [
      {
        id: "file_agent_notes",
        created_at: 1_744_288_820,
        status: "completed",
        usage_bytes: 9_216,
        vector_store_id: "vs_demo_agent",
        attributes: { source: "agent-notes" },
        last_error: null,
      },
    ],
    batch: null,
    batch_files: [],
  },
};

const DOCUMENTS: MockDocument[] = [
  {
    vector_store_id: "vs_demo_ops",
    file_id: "file_ops_alpha",
    filename: "incident-runbook.md",
    text: "Escalate pager alerts to the platform rotation and verify vector store ingestion before retrying agent retrieval.",
  },
  {
    vector_store_id: "vs_demo_ops",
    file_id: "file_ops_beta",
    filename: "search-tuning.md",
    text: "Use exact markers for smoke tests and prefer raw search before ask_vector_store when validating retrieval quality.",
  },
  {
    vector_store_id: "vs_demo_agent",
    file_id: "file_agent_notes",
    filename: "agent-capabilities.md",
    text: "The VS Code agent can use OpenAI files and vector stores as additional retrieval context through the MCP console.",
  },
];

function wait(durationMs: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, durationMs));
}

function scoreHit(query: string, text: string): number {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return 0;
  }
  const normalizedText = text.toLowerCase();
  if (normalizedText.includes(normalizedQuery)) {
    return 0.97;
  }
  const queryTerms = normalizedQuery.split(/\s+/);
  const matches = queryTerms.filter((term) => normalizedText.includes(term)).length;
  return matches === 0 ? 0 : matches / queryTerms.length;
}

function buildSearchHits(vector_store_id: string, query: string, max_num_results: number): SearchHit[] {
  return DOCUMENTS.filter((document) => document.vector_store_id === vector_store_id)
    .map((document) => ({
      file_id: document.file_id,
      filename: document.filename,
      score: scoreHit(query, document.text),
      text: document.text,
      attributes: null,
    }))
    .filter((hit) => hit.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, max_num_results);
}

export function createMockBridge(): VectorStoreConsoleBridge {
  const initialVectorStoreId = VECTOR_STORE_LIST.vector_stores[0]?.id ?? null;
  const initial_state: OpenVectorStoreConsoleResult = {
    vector_store_list: VECTOR_STORE_LIST,
    selected_vector_store_id: initialVectorStoreId,
    selected_vector_store_status: initialVectorStoreId === null ? null : STATUS_BY_ID[initialVectorStoreId],
    search_panel: {
      query: "",
      max_num_results: 5,
      rewrite_query: false,
    },
    ask_panel: {
      question: "",
      max_num_results: 5,
    },
  };

  return {
    mode: "mock",
    hostContext: HOST_CONTEXT,
    initial_state,
    async list_vector_stores() {
      await wait(120);
      return VECTOR_STORE_LIST;
    },
    async get_vector_store_status(args: GetVectorStoreStatusArguments) {
      await wait(160);
      const status = STATUS_BY_ID[args.vector_store_id];
      if (!status) {
        throw new Error(`Unknown mock vector store: ${args.vector_store_id}`);
      }
      return status;
    },
    async search_vector_store(args: SearchVectorStoreArguments): Promise<SearchVectorStoreResult> {
      await wait(180);
      const hits = buildSearchHits(args.vector_store_id, args.query, args.max_num_results ?? 5);
      return {
        vector_store_id: args.vector_store_id,
        query: args.query,
        hits,
        total_hits: hits.length,
      };
    },
    async ask_vector_store(args: AskVectorStoreArguments): Promise<AskVectorStoreResult> {
      await wait(240);
      const hits = buildSearchHits(args.vector_store_id, args.question, args.max_num_results ?? 5);
      const answer =
        hits.length > 0
          ? `Based on the indexed content, ${hits[0].text}`
          : "I could not find grounded support for that question in the selected vector store.";

      return {
        vector_store_id: args.vector_store_id,
        question: args.question,
        answer,
        model: "mock-file-search-agent",
        search_calls: [
          {
            id: "mock-search-call-1",
            status: "completed",
            queries: [args.question],
            results: hits,
          },
        ],
      };
    },
    async preview_file(args: PreviewFileArguments): Promise<FilePreviewResult> {
      await wait(140);
      const document = DOCUMENTS.find((candidate) => candidate.file_id === args.file_id);
      if (!document) {
        throw new Error(`Unknown mock file: ${args.file_id}`);
      }

      const previewText = document.text.slice(0, args.max_chars ?? 32_768);
      return {
        vector_store_id: document.vector_store_id,
        file_id: document.file_id,
        filename: document.filename,
        bytes: new TextEncoder().encode(document.text).length,
        purpose: "assistants",
        status: "processed",
        preview_text: previewText,
        preview_truncated: previewText.length < document.text.length,
        preview_message:
          previewText.length < document.text.length
            ? `Showing the first ${args.max_chars ?? 32_768} characters of the parsed file content.`
            : null,
      };
    },
  };
}
