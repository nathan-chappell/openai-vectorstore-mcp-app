import cors from "cors";
import express from "express";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { McpUiResourceCsp } from "@modelcontextprotocol/ext-apps";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const hostBindAddress = process.env.HOST_BIND_ADDRESS ?? "127.0.0.1";
const hostPort = Number.parseInt(process.env.HOST_PORT ?? "8080", 10);
const sandboxPort = Number.parseInt(process.env.SANDBOX_PORT ?? "8081", 10);
const distDirectory = join(__dirname, "..", "host-dist");
const serverUrls = process.env.DEV_HOST_SERVERS ? JSON.parse(process.env.DEV_HOST_SERVERS) as string[] : ["http://127.0.0.1:8000/mcp"];

function sanitizeCspDomains(domains?: string[]): string[] {
  if (!Array.isArray(domains)) {
    return [];
  }

  return domains.filter((domain) => !/[;\r\n'" ]/.test(domain));
}

function buildCspHeader(csp?: McpUiResourceCsp): string {
  const resourceDomains = sanitizeCspDomains(csp?.resourceDomains).join(" ");
  const connectDomains = sanitizeCspDomains(csp?.connectDomains).join(" ");
  const frameDomains = sanitizeCspDomains(csp?.frameDomains).join(" ");
  const baseUriDomains = sanitizeCspDomains(csp?.baseUriDomains).join(" ");

  const directives = [
    "default-src 'self' 'unsafe-inline'",
    `script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data: ${resourceDomains}`.trim(),
    `style-src 'self' 'unsafe-inline' blob: data: ${resourceDomains}`.trim(),
    `img-src 'self' data: blob: ${resourceDomains}`.trim(),
    `font-src 'self' data: blob: ${resourceDomains}`.trim(),
    `media-src 'self' data: blob: ${resourceDomains}`.trim(),
    `connect-src 'self' ${connectDomains}`.trim(),
    `worker-src 'self' blob: ${resourceDomains}`.trim(),
    frameDomains ? `frame-src ${frameDomains}` : "frame-src 'none'",
    "object-src 'none'",
    baseUriDomains ? `base-uri ${baseUriDomains}` : "base-uri 'none'",
  ];

  return directives.join("; ");
}

const hostApp = express();
hostApp.use(cors());
hostApp.use((request, response, next) => {
  if (request.path === "/sandbox.html") {
    response.status(404).send("sandbox.html is served on the sandbox port.");
    return;
  }

  next();
});
hostApp.use(express.static(distDirectory));
hostApp.get("/api/servers", (_request, response) => {
  response.json(serverUrls);
});
hostApp.get("/", (_request, response) => {
  response.redirect("/index.html");
});

const sandboxApp = express();
sandboxApp.use(cors());
sandboxApp.get(["/", "/sandbox.html"], (request, response) => {
  let csp: McpUiResourceCsp | undefined;
  if (typeof request.query.csp === "string") {
    try {
      csp = JSON.parse(request.query.csp) as McpUiResourceCsp;
    } catch (error) {
      console.warn("[dev-host] Ignoring invalid sandbox CSP payload:", error);
    }
  }

  response.setHeader("Content-Security-Policy", buildCspHeader(csp));
  response.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  response.setHeader("Pragma", "no-cache");
  response.setHeader("Expires", "0");
  response.sendFile(join(distDirectory, "sandbox.html"));
});
sandboxApp.use((_request, response) => {
  response.status(404).send("Only sandbox.html is served on the sandbox port.");
});

hostApp.listen(hostPort, hostBindAddress, () => {
  console.log(`[dev-host] Host UI ready at http://${hostBindAddress}:${hostPort}`);
  console.log(`[dev-host] MCP servers: ${serverUrls.join(", ")}`);
});

sandboxApp.listen(sandboxPort, hostBindAddress, () => {
  console.log(`[dev-host] Sandbox ready at http://${hostBindAddress}:${sandboxPort}/sandbox.html`);
});
