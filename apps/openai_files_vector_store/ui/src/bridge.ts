import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";

import type {
  AskVectorStoreArguments,
  AskVectorStoreResult,
  FilePreviewResult,
  GetVectorStoreStatusArguments,
  ListVectorStoresArguments,
  OpenVectorStoreConsoleResult,
  PreviewFileArguments,
  SearchVectorStoreArguments,
  SearchVectorStoreResult,
  ToolResultName,
  VectorStoreListResult,
  VectorStoreStatusResult,
} from "./types";
import { getStructuredContent } from "./types";

export interface VectorStoreConsoleBridge {
  readonly mode: "host" | "mock";
  readonly hostContext?: McpUiHostContext;
  readonly initial_state: OpenVectorStoreConsoleResult;
  list_vector_stores(args: ListVectorStoresArguments): Promise<VectorStoreListResult>;
  get_vector_store_status(args: GetVectorStoreStatusArguments): Promise<VectorStoreStatusResult>;
  search_vector_store(args: SearchVectorStoreArguments): Promise<SearchVectorStoreResult>;
  ask_vector_store(args: AskVectorStoreArguments): Promise<AskVectorStoreResult>;
  preview_file(args: PreviewFileArguments): Promise<FilePreviewResult>;
}

async function callStructuredTool<T>(
  app: App,
  name: ToolResultName,
  args:
    | ListVectorStoresArguments
    | GetVectorStoreStatusArguments
    | SearchVectorStoreArguments
    | AskVectorStoreArguments
    | PreviewFileArguments,
): Promise<T> {
  const result = await app.callServerTool({
    name,
    arguments: args as Record<string, unknown>,
  });
  return getStructuredContent<T>(result);
}

export function createHostBridge(
  app: App,
  initial_state: OpenVectorStoreConsoleResult,
  hostContext?: McpUiHostContext,
): VectorStoreConsoleBridge {
  return {
    mode: "host",
    hostContext,
    initial_state,
    list_vector_stores(args) {
      return callStructuredTool<VectorStoreListResult>(app, "list_vector_stores", args);
    },
    get_vector_store_status(args) {
      return callStructuredTool<VectorStoreStatusResult>(app, "get_vector_store_status", args);
    },
    search_vector_store(args) {
      return callStructuredTool<SearchVectorStoreResult>(app, "search_vector_store", args);
    },
    ask_vector_store(args) {
      return callStructuredTool<AskVectorStoreResult>(app, "ask_vector_store", args);
    },
    preview_file(args) {
      return callStructuredTool<FilePreviewResult>(app, "preview_file", args);
    },
  };
}
