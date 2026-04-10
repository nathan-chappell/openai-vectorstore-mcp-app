import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

export interface FileCountsSummary {
  completed: number;
  failed: number;
  in_progress: number;
  cancelled: number;
  total: number;
}

export interface FilePreviewResult {
  vector_store_id: string;
  file_id: string;
  filename: string;
  bytes: number;
  purpose: string;
  status: string;
  preview_text: string | null;
  preview_truncated: boolean;
  preview_message: string | null;
}

export interface VectorStoreSummary {
  id: string;
  name: string;
  status: string;
  created_at: number;
  last_active_at: number | null;
  usage_bytes: number;
  expires_at: number | null;
  metadata: Record<string, string> | null;
  file_counts: FileCountsSummary;
}

export interface VectorStoreFileSummary {
  id: string;
  created_at: number;
  status: string;
  usage_bytes: number;
  vector_store_id: string;
  attributes: Record<string, string | number | boolean> | null;
  last_error: string | null;
}

export interface VectorStoreBatchSummary {
  id: string;
  created_at: number;
  status: string;
  vector_store_id: string;
  file_counts: FileCountsSummary;
}

export interface SearchHit {
  file_id: string;
  filename: string;
  score: number;
  text: string;
  attributes: Record<string, string | number | boolean> | null;
}

export interface FileSearchCallSummary {
  id: string;
  status: string;
  queries: string[];
  results: SearchHit[];
}

export interface VectorStoreListResult {
  vector_stores: VectorStoreSummary[];
  total_returned: number;
}

export interface VectorStoreStatusResult {
  vector_store: VectorStoreSummary;
  files: VectorStoreFileSummary[];
  batch: VectorStoreBatchSummary | null;
  batch_files: VectorStoreFileSummary[];
}

export interface SearchVectorStoreResult {
  vector_store_id: string;
  query: string;
  hits: SearchHit[];
  total_hits: number;
}

export interface AskVectorStoreResult {
  vector_store_id: string;
  question: string;
  answer: string;
  model: string;
  search_calls: FileSearchCallSummary[];
}

export interface SearchPanelState {
  query: string;
  max_num_results: number;
  rewrite_query: boolean;
}

export interface AskPanelState {
  question: string;
  max_num_results: number;
}

export interface OpenVectorStoreConsoleResult {
  vector_store_list: VectorStoreListResult;
  selected_vector_store_id: string | null;
  selected_vector_store_status: VectorStoreStatusResult | null;
  search_panel: SearchPanelState;
  ask_panel: AskPanelState;
}

export interface ListVectorStoresArguments {
  limit?: number;
}

export interface GetVectorStoreStatusArguments {
  vector_store_id: string;
  file_limit?: number;
  batch_id?: string | null;
}

export interface SearchVectorStoreArguments {
  vector_store_id: string;
  query: string;
  max_num_results?: number;
  rewrite_query?: boolean;
}

export interface AskVectorStoreArguments {
  vector_store_id: string;
  question: string;
  max_num_results?: number;
}

export interface PreviewFileArguments {
  vector_store_id: string;
  file_id: string;
  max_chars?: number;
}

export type ToolResultName = "list_vector_stores" | "get_vector_store_status" | "search_vector_store" | "ask_vector_store" | "preview_file";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function getStructuredContent<T>(result: CallToolResult): T {
  if (!isRecord(result.structuredContent)) {
    throw new Error("Expected structuredContent in the tool result.");
  }
  return result.structuredContent as T;
}

export function getTextContent(result: CallToolResult): string {
  const block = result.content.find((item): item is Extract<CallToolResult["content"][number], { type: "text" }> => item.type === "text");
  return block?.text ?? "";
}

export function isOpenVectorStoreConsoleResult(value: unknown): value is OpenVectorStoreConsoleResult {
  return isRecord(value) && "vector_store_list" in value && "search_panel" in value && "ask_panel" in value;
}
