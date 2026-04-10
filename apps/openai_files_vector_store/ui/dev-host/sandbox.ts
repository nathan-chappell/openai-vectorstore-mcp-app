import type { McpUiResourcePermissions, McpUiSandboxProxyReadyNotification, McpUiSandboxResourceReadyNotification } from "@modelcontextprotocol/ext-apps/app-bridge";
import { buildAllowAttribute } from "@modelcontextprotocol/ext-apps/app-bridge";

const ALLOWED_REFERRER_PATTERN = /^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/;

if (window.self === window.top) {
  throw new Error("sandbox.html must run inside an iframe.");
}

if (document.referrer.length === 0) {
  throw new Error("Missing referrer. The dev host sandbox cannot validate its parent origin.");
}

if (!ALLOWED_REFERRER_PATTERN.test(document.referrer)) {
  throw new Error(`Unexpected embedding origin: ${document.referrer}`);
}

const expectedHostOrigin = new URL(document.referrer).origin;
const ownOrigin = window.location.origin;
const innerFrame = document.createElement("iframe");

innerFrame.style.width = "100%";
innerFrame.style.height = "100%";
innerFrame.style.border = "0";
innerFrame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
document.body.appendChild(innerFrame);

const proxyReadyMethod: McpUiSandboxProxyReadyNotification["method"] = "ui/notifications/sandbox-proxy-ready";
const resourceReadyMethod: McpUiSandboxResourceReadyNotification["method"] = "ui/notifications/sandbox-resource-ready";

window.addEventListener("message", (event) => {
  if (event.source === window.parent) {
    if (event.origin !== expectedHostOrigin) {
      console.warn("[sandbox] Ignoring message from unexpected host origin:", event.origin);
      return;
    }

    if (event.data?.method === resourceReadyMethod) {
      const sandboxParameters = event.data.params as {
        html?: string;
        permissions?: McpUiResourcePermissions;
        sandbox?: string;
      };

      if (typeof sandboxParameters.sandbox === "string") {
        innerFrame.setAttribute("sandbox", sandboxParameters.sandbox);
      }

      const allowAttribute = buildAllowAttribute(sandboxParameters.permissions);
      if (allowAttribute) {
        innerFrame.setAttribute("allow", allowAttribute);
      }

      if (typeof sandboxParameters.html === "string") {
        const innerDocument = innerFrame.contentDocument ?? innerFrame.contentWindow?.document;
        if (innerDocument) {
          innerDocument.open();
          innerDocument.write(sandboxParameters.html);
          innerDocument.close();
        } else {
          innerFrame.srcdoc = sandboxParameters.html;
        }
      }

      return;
    }

    innerFrame.contentWindow?.postMessage(event.data, "*");
    return;
  }

  if (event.source === innerFrame.contentWindow) {
    if (event.origin !== ownOrigin) {
      console.warn("[sandbox] Ignoring message from unexpected inner origin:", event.origin);
      return;
    }

    window.parent.postMessage(event.data, expectedHostOrigin);
  }
});

window.parent.postMessage(
  {
    jsonrpc: "2.0",
    method: proxyReadyMethod,
    params: {},
  },
  expectedHostOrigin,
);
