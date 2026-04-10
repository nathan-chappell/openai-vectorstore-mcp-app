import type { AppBridge as AppBridgeInstance, McpUiMessageRequest, McpUiResourceCsp, McpUiResourcePermissions, McpUiUpdateModelContextRequest } from "@modelcontextprotocol/ext-apps/app-bridge";
import { AppBridge, PostMessageTransport, RESOURCE_MIME_TYPE, buildAllowAttribute, getToolUiResourceUri, McpUiToolMetaSchema } from "@modelcontextprotocol/ext-apps/app-bridge";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { CallToolResult, Resource, Tool } from "@modelcontextprotocol/sdk/types.js";

const IMPLEMENTATION = {
  name: "OpenAI Files Vector Store Dev Host",
  version: "1.0.0",
};

type ToolResourceContent = {
  mimeType?: string;
  text?: string;
  blob?: string;
  _meta?: Record<string, unknown>;
  meta?: Record<string, unknown>;
};

export interface ServerInfo {
  readonly name: string;
  readonly url: string;
  readonly client: Client;
  readonly tools: Map<string, Tool>;
  readonly resources: Map<string, Resource>;
}

export interface UiResourceData {
  readonly html: string;
  readonly csp?: McpUiResourceCsp;
  readonly permissions?: McpUiResourcePermissions;
}

export type ModelContext = McpUiUpdateModelContextRequest["params"] | null;
export type AppMessage = McpUiMessageRequest["params"];
export type DisplayMode = "inline" | "fullscreen";

export interface AppBridgeCallbacks {
  readonly onContextUpdate: (context: ModelContext) => void;
  readonly onDisplayModeChange: (mode: DisplayMode) => void;
  readonly onMessage: (message: AppMessage) => void;
}

function getCurrentTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function sanitizeErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function getSandboxProxyUrl(): URL {
  const url = new URL(window.location.href);
  url.port = "8081";
  url.pathname = "/sandbox.html";
  url.search = "";
  url.hash = "";
  return url;
}

function isUiMetaRecord(value: unknown): value is {
  ui?: {
    csp?: McpUiResourceCsp;
    permissions?: McpUiResourcePermissions;
  };
} {
  return typeof value === "object" && value !== null;
}

async function connectWithFallback(serverUrl: URL): Promise<Client> {
  try {
    const client = new Client(IMPLEMENTATION);
    await client.connect(new StreamableHTTPClientTransport(serverUrl));
    return client;
  } catch {
    const client = new Client(IMPLEMENTATION);
    await client.connect(new SSEClientTransport(serverUrl));
    return client;
  }
}

export async function connectToServer(serverUrl: URL): Promise<ServerInfo> {
  const client = await connectWithFallback(serverUrl);
  const toolList = await client.listTools();
  const resourceList = await client.listResources();

  return {
    name: client.getServerVersion()?.name ?? serverUrl.href,
    url: serverUrl.href,
    client,
    tools: new Map(toolList.tools.map((tool) => [tool.name, tool])),
    resources: new Map(resourceList.resources.map((resource) => [resource.uri, resource])),
  };
}

export function isToolVisibleToModel(tool: Tool): boolean {
  const parsed = McpUiToolMetaSchema.safeParse(tool._meta?.ui);
  if (!parsed.success || !parsed.data.visibility) {
    return true;
  }

  return parsed.data.visibility.includes("model");
}

export function compareTools(left: Tool, right: Tool): number {
  const leftHasUi = getToolUiResourceUri(left) !== undefined;
  const rightHasUi = getToolUiResourceUri(right) !== undefined;
  if (leftHasUi !== rightHasUi) {
    return leftHasUi ? -1 : 1;
  }
  return left.name.localeCompare(right.name);
}

export function getToolDefaultInput(tool: Tool | null | undefined): string {
  if (!tool || typeof tool.inputSchema !== "object" || tool.inputSchema === null) {
    return "{}";
  }

  const schemaProperties = "properties" in tool.inputSchema ? tool.inputSchema.properties : undefined;
  if (!schemaProperties || typeof schemaProperties !== "object") {
    return "{}";
  }

  const defaults: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(schemaProperties)) {
    if (typeof value === "object" && value !== null && "default" in value) {
      defaults[key] = (value as { default: unknown }).default;
    }
  }

  return Object.keys(defaults).length === 0 ? "{}" : JSON.stringify(defaults, null, 2);
}

export async function readUiResource(serverInfo: ServerInfo, uri: string): Promise<UiResourceData> {
  const resource = await serverInfo.client.readResource({ uri });
  if (resource.contents.length !== 1) {
    throw new Error(`Expected one UI resource content block for ${uri}.`);
  }

  const content = resource.contents[0] as ToolResourceContent;
  if (content.mimeType !== RESOURCE_MIME_TYPE) {
    throw new Error(`Unsupported UI resource mime type: ${content.mimeType ?? "unknown"}`);
  }

  const html = typeof content.blob === "string" ? atob(content.blob) : content.text;
  if (typeof html !== "string") {
    throw new Error(`UI resource ${uri} did not include HTML content.`);
  }

  const listing = serverInfo.resources.get(uri) as (Resource & { _meta?: Record<string, unknown> }) | undefined;
  const contentMeta = isUiMetaRecord(content._meta) ? content._meta : isUiMetaRecord(content.meta) ? content.meta : undefined;
  const listingMeta = isUiMetaRecord(listing?._meta) ? listing._meta : undefined;
  const uiMeta = contentMeta?.ui ?? listingMeta?.ui;

  return {
    html,
    csp: uiMeta?.csp,
    permissions: uiMeta?.permissions,
  };
}

export async function loadSandboxProxy(
  iframe: HTMLIFrameElement,
  csp?: McpUiResourceCsp,
  permissions?: McpUiResourcePermissions,
): Promise<void> {
  iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");

  const allowAttribute = buildAllowAttribute(permissions);
  if (allowAttribute) {
    iframe.setAttribute("allow", allowAttribute);
  }

  await new Promise<void>((resolve) => {
    const listener = (event: MessageEvent) => {
      if (event.source !== iframe.contentWindow) {
        return;
      }

      if (event.data?.method === "ui/notifications/sandbox-proxy-ready") {
        window.removeEventListener("message", listener);
        resolve();
      }
    };

    window.addEventListener("message", listener);
    const sandboxUrl = getSandboxProxyUrl();
    if (csp) {
      sandboxUrl.searchParams.set("csp", JSON.stringify(csp));
    }
    iframe.src = sandboxUrl.toString();
  });
}

function waitForInitialization(appBridge: AppBridgeInstance): Promise<void> {
  return new Promise((resolve) => {
    const previousHandler = appBridge.oninitialized;
    appBridge.oninitialized = (...args) => {
      resolve();
      appBridge.oninitialized = previousHandler;
      previousHandler?.(...args);
    };
  });
}

export function createAppBridge(
  serverInfo: ServerInfo,
  iframe: HTMLIFrameElement,
  callbacks: AppBridgeCallbacks,
): AppBridgeInstance {
  const serverCapabilities = serverInfo.client.getServerCapabilities();
  const appBridge = new AppBridge(
    serverInfo.client,
    IMPLEMENTATION,
    {
      openLinks: {},
      serverResources: serverCapabilities?.resources,
      serverTools: serverCapabilities?.tools,
      updateModelContext: { text: {} },
    },
    {
      hostContext: {
        theme: getCurrentTheme(),
        platform: "web",
        locale: navigator.language,
        userAgent: navigator.userAgent,
        displayMode: "inline",
        availableDisplayModes: ["inline", "fullscreen"],
        safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
        containerDimensions: { width: iframe.clientWidth, maxHeight: 6000 },
      },
    },
  );

  const resizeObserver = new ResizeObserver(([entry]) => {
    const width = Math.round(entry.contentRect.width);
    if (width > 0) {
      appBridge.sendHostContextChange({
        containerDimensions: {
          width,
          maxHeight: 6000,
        },
      });
    }
  });
  resizeObserver.observe(iframe);

  const themeQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const onThemeChange = (event: MediaQueryListEvent) => {
    appBridge.sendHostContextChange({
      theme: event.matches ? "dark" : "light",
    });
  };
  themeQuery.addEventListener("change", onThemeChange);

  const previousOnClose = appBridge.onclose;
  appBridge.onclose = () => {
    resizeObserver.disconnect();
    themeQuery.removeEventListener("change", onThemeChange);
    previousOnClose?.();
  };

  appBridge.onloggingmessage = (params) => {
    console.info("[dev-host]", params);
  };

  appBridge.onmessage = async (params) => {
    callbacks.onMessage(params);
    return {};
  };

  appBridge.onopenlink = async (params) => {
    window.open(params.url, "_blank", "noopener,noreferrer");
    return {};
  };

  appBridge.onupdatemodelcontext = async (params) => {
    const hasContent = (params.content?.length ?? 0) > 0;
    const hasStructuredContent = params.structuredContent !== undefined && Object.keys(params.structuredContent).length > 0;
    callbacks.onContextUpdate(hasContent || hasStructuredContent ? params : null);
    return {};
  };

  appBridge.onsizechange = async ({ height, width }) => {
    if (width !== undefined) {
      iframe.style.minWidth = `min(${Math.ceil(width)}px, 100%)`;
    }

    if (height !== undefined) {
      iframe.style.height = `${Math.ceil(height)}px`;
    }
  };

  appBridge.onrequestdisplaymode = async ({ mode }) => {
    const nextMode: DisplayMode = mode === "fullscreen" ? "fullscreen" : "inline";
    callbacks.onDisplayModeChange(nextMode);
    appBridge.sendHostContextChange({ displayMode: nextMode });
    return { mode: nextMode };
  };

  return appBridge;
}

export async function invokeToolWithOptionalUi(
  serverInfo: ServerInfo,
  tool: Tool,
  input: Record<string, unknown>,
  iframe: HTMLIFrameElement | null,
  appBridge: AppBridgeInstance | null,
): Promise<CallToolResult> {
  const resultPromise = serverInfo.client.callTool({
    name: tool.name,
    arguments: input,
  }) as Promise<CallToolResult>;

  const resourceUri = getToolUiResourceUri(tool);
  if (!resourceUri || iframe === null || appBridge === null) {
    return resultPromise;
  }

  const uiResource = await readUiResource(serverInfo, resourceUri);
  await loadSandboxProxy(iframe, uiResource.csp, uiResource.permissions);

  const initialized = waitForInitialization(appBridge);
  await appBridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
  await appBridge.sendSandboxResourceReady({
    html: uiResource.html,
    csp: uiResource.csp,
    permissions: uiResource.permissions,
  });
  await initialized;
  appBridge.sendToolInput({ arguments: input });

  try {
    const result = await resultPromise;
    await appBridge.sendToolResult(result);
    return result;
  } catch (error) {
    await appBridge.sendToolCancelled({
      reason: sanitizeErrorMessage(error),
    });
    throw error;
  }
}

export function getVisibleTools(serverInfo: ServerInfo | null): Tool[] {
  if (serverInfo === null) {
    return [];
  }

  return Array.from(serverInfo.tools.values()).filter(isToolVisibleToModel).sort(compareTools);
}

export function getToolUiUri(tool: Tool | null): string | undefined {
  return tool === null ? undefined : getToolUiResourceUri(tool);
}
