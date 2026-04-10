import type { AppBridge as AppBridgeInstance } from "@modelcontextprotocol/ext-apps/app-bridge";
import type { Tool } from "@modelcontextprotocol/sdk/types.js";
import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState } from "react";

import { connectToServer, createAppBridge, getToolDefaultInput, getToolUiUri, getVisibleTools, invokeToolWithOptionalUi, type AppMessage, type DisplayMode, type ModelContext, type ServerInfo } from "./mcpHost";

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatPanelJson(value: unknown, emptyMessage: string): string {
  return value === null || value === undefined ? emptyMessage : JSON.stringify(value, null, 2);
}

function App() {
  const [serverInfos, setServerInfos] = useState<ServerInfo[]>([]);
  const [selectedServerName, setSelectedServerName] = useState("");
  const [selectedToolName, setSelectedToolName] = useState("");
  const [inputJson, setInputJson] = useState("{}");
  const [isConnecting, setIsConnecting] = useState(true);
  const [isCalling, setIsCalling] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [callError, setCallError] = useState<string | null>(null);
  const [resultJson, setResultJson] = useState("");
  const [messages, setMessages] = useState<AppMessage[]>([]);
  const [modelContext, setModelContext] = useState<ModelContext>(null);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("inline");

  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const bridgeRef = useRef<AppBridgeInstance | null>(null);

  const selectedServer = serverInfos.find((serverInfo) => serverInfo.name === selectedServerName) ?? null;
  const visibleTools = getVisibleTools(selectedServer);
  const selectedTool = visibleTools.find((tool) => tool.name === selectedToolName) ?? visibleTools[0] ?? null;
  const uiUri = getToolUiUri(selectedTool);

  let parsedInput: Record<string, unknown> | null = null;
  let inputValidationMessage: string | null = null;
  try {
    const candidate = JSON.parse(inputJson) as unknown;
    if (!isJsonObject(candidate)) {
      inputValidationMessage = "Tool input must be a JSON object.";
    } else {
      parsedInput = candidate;
    }
  } catch (error) {
    inputValidationMessage = formatErrorMessage(error);
  }

  useEffect(() => {
    let isCancelled = false;

    async function loadServers(): Promise<void> {
      setIsConnecting(true);
      setLoadError(null);

      try {
        const response = await fetch("/api/servers");
        if (!response.ok) {
          throw new Error(`Unable to load configured servers (${response.status}).`);
        }

        const serverUrls = (await response.json()) as string[];
        if (serverUrls.length === 0) {
          throw new Error("No MCP servers are configured for the dev host.");
        }

        const settledConnections = await Promise.allSettled(
          serverUrls.map((serverUrl) => connectToServer(new URL(serverUrl))),
        );
        if (isCancelled) {
          return;
        }

        const connectedServers = settledConnections.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []));
        if (connectedServers.length === 0) {
          throw new Error("The dev host could not connect to any configured MCP server.");
        }

        setServerInfos(connectedServers);
        const firstServer = connectedServers[0];
        const firstTool = getVisibleTools(firstServer)[0] ?? null;
        setSelectedServerName(firstServer.name);
        setSelectedToolName(firstTool?.name ?? "");
        setInputJson(getToolDefaultInput(firstTool));
      } catch (error) {
        if (!isCancelled) {
          setLoadError(formatErrorMessage(error));
        }
      } finally {
        if (!isCancelled) {
          setIsConnecting(false);
        }
      }
    }

    void loadServers();

    return () => {
      isCancelled = true;
      if (bridgeRef.current !== null) {
        void bridgeRef.current.close().catch(() => undefined);
      }
    };
  }, []);

  async function resetCurrentApp(): Promise<void> {
    if (bridgeRef.current !== null) {
      try {
        await bridgeRef.current.teardownResource({});
      } catch {
        // Ignore teardown races during rapid iteration.
      }

      await bridgeRef.current.close().catch(() => undefined);
      bridgeRef.current = null;
    }

    setDisplayMode("inline");

    if (iframeRef.current !== null) {
      iframeRef.current.src = "";
      iframeRef.current.style.height = "720px";
      iframeRef.current.style.minWidth = "0";
    }
  }

  function handleServerChange(nextServerName: string): void {
    setSelectedServerName(nextServerName);

    const nextServer = serverInfos.find((serverInfo) => serverInfo.name === nextServerName) ?? null;
    const firstTool = getVisibleTools(nextServer)[0] ?? null;
    setSelectedToolName(firstTool?.name ?? "");
    setInputJson(getToolDefaultInput(firstTool));
    setCallError(null);
    void resetCurrentApp();
  }

  function handleToolChange(nextToolName: string): void {
    setSelectedToolName(nextToolName);

    const nextTool = visibleTools.find((tool) => tool.name === nextToolName) ?? null;
    setInputJson(getToolDefaultInput(nextTool));
    setCallError(null);
    void resetCurrentApp();
  }

  async function handleCallTool(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selectedServer === null || selectedTool === null || parsedInput === null) {
      return;
    }

    setIsCalling(true);
    setCallError(null);
    setResultJson("");
    setMessages([]);
    setModelContext(null);

    await resetCurrentApp();

    try {
      const shouldRenderUi = uiUri !== undefined && iframeRef.current !== null;
      if (shouldRenderUi && iframeRef.current !== null) {
        bridgeRef.current = createAppBridge(selectedServer, iframeRef.current, {
          onContextUpdate: setModelContext,
          onDisplayModeChange: setDisplayMode,
          onMessage: (message) => {
            setMessages((previous) => [...previous, message]);
          },
        });
      }

      const result = await invokeToolWithOptionalUi(
        selectedServer,
        selectedTool,
        parsedInput,
        iframeRef.current,
        bridgeRef.current,
      );
      setResultJson(JSON.stringify(result, null, 2));
    } catch (error) {
      setCallError(formatErrorMessage(error));
    } finally {
      setIsCalling(false);
    }
  }

  function exitFullscreen(): void {
    setDisplayMode("inline");
    if (bridgeRef.current !== null) {
      bridgeRef.current.sendHostContextChange({ displayMode: "inline" });
    }
  }

  const toolSupportsUi = uiUri !== undefined;

  return (
    <div className="host-shell">
      <section className="host-card host-hero">
        <div className="host-kicker">MCP App Dev Loop</div>
        <h1>Test the real hosted app without leaving `npm run dev`.</h1>
        <p>
          This local harness is adapted from the upstream `modelcontextprotocol/ext-apps`
          `basic-host` example. It connects to the Python MCP server over streamable HTTP,
          lets you call tools directly, and renders the app inside the same sandbox pattern
          the upstream test client uses.
        </p>
      </section>

      <div className="host-grid">
        <form className="host-card host-controls" onSubmit={(event) => void handleCallTool(event)}>
          <div className="host-field">
            <label htmlFor="server-select">Server</label>
            <select
              id="server-select"
              disabled={isConnecting || serverInfos.length === 0}
              value={selectedServerName}
              onChange={(event) => handleServerChange(event.target.value)}
            >
              {serverInfos.map((serverInfo) => (
                <option key={serverInfo.name} value={serverInfo.name}>
                  {serverInfo.name}
                </option>
              ))}
            </select>
          </div>

          <div className="host-field">
            <label htmlFor="tool-select">Tool</label>
            <select
              id="tool-select"
              disabled={selectedServer === null || visibleTools.length === 0}
              value={selectedTool?.name ?? ""}
              onChange={(event) => handleToolChange(event.target.value)}
            >
              {visibleTools.map((tool) => (
                <option key={tool.name} value={tool.name}>
                  {tool.name}
                </option>
              ))}
            </select>
          </div>

          <div className="host-field">
            <label htmlFor="tool-input">Tool input</label>
            <textarea
              id="tool-input"
              aria-invalid={inputValidationMessage !== null}
              spellCheck={false}
              value={inputJson}
              onChange={(event) => setInputJson(event.target.value)}
            />
          </div>

          <div className="host-actions">
            <button className="host-button" disabled={isConnecting || isCalling || selectedTool === null || parsedInput === null} type="submit">
              {isCalling ? "Calling tool..." : "Call tool"}
            </button>
            <button className="host-button host-button--secondary" onClick={() => setInputJson(getToolDefaultInput(selectedTool))} type="button">
              Reset input
            </button>
          </div>

          <div className="host-status">
            {isConnecting ? "Connecting to the configured MCP server..." : selectedServer === null ? "No server connected." : `Connected to ${selectedServer.url}`}
          </div>

          {inputValidationMessage !== null ? <div className="host-alert">{inputValidationMessage}</div> : null}
          {loadError !== null ? <div className="host-alert">{loadError}</div> : null}
          {callError !== null ? <div className="host-alert">{callError}</div> : null}
        </form>

        <section className="host-card host-surface">
          <div className="host-surface-header">
            <div>
              <h2>Rendered app</h2>
            </div>
            <div className="host-tag">
              {selectedTool === null ? "No tool selected" : toolSupportsUi ? `UI tool: ${selectedTool.name}` : `No UI resource on ${selectedTool.name}`}
            </div>
          </div>

          {toolSupportsUi ? (
            <div className={`host-preview${displayMode === "fullscreen" ? " fullscreen" : ""}`}>
              {displayMode === "fullscreen" ? (
                <div className="host-surface-header" style={{ padding: "14px 18px 0" }}>
                  <div className="host-tag">App requested fullscreen mode</div>
                  <button className="host-button host-button--secondary" onClick={exitFullscreen} type="button">
                    Exit fullscreen
                  </button>
                </div>
              ) : null}
              <iframe ref={iframeRef} title="MCP app preview" />
            </div>
          ) : (
            <div className="host-preview-empty">
              <div>
                This tool does not declare a UI resource.
                <br />
                Its JSON result still appears below so you can exercise the server from the same screen.
              </div>
            </div>
          )}
        </section>
      </div>

      <section className="host-panels">
        <article className="host-card host-panel">
          <div className="host-surface-header">
            <h3>Tool result</h3>
            <div className="host-tag">{resultJson ? "latest call" : "waiting"}</div>
          </div>
          <p>The raw MCP result is always shown here, even for non-UI tools.</p>
          <pre className="host-code">{resultJson || "Call a tool to inspect its result."}</pre>
        </article>

        <article className="host-card host-panel">
          <div className="host-surface-header">
            <h3>App messages</h3>
            <div className="host-tag">{messages.length === 0 ? "none yet" : `${messages.length} message${messages.length === 1 ? "" : "s"}`}</div>
          </div>
          <p>Messages sent from the app to the host or model surface appear here.</p>
          <pre className="host-code">{formatPanelJson(messages.length === 0 ? null : messages, "No app messages yet.")}</pre>
        </article>

        <article className="host-card host-panel wide">
          <div className="host-surface-header">
            <h3>Model context</h3>
            <div className="host-tag">{modelContext === null ? "empty" : "updated"}</div>
          </div>
          <p>When the app calls `updateModelContext`, the host stores the most recent payload here for inspection.</p>
          <pre className="host-code">{formatPanelJson(modelContext, "The app has not published any model context yet.")}</pre>
        </article>
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
