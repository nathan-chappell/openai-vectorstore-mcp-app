import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ClerkProvider } from "@clerk/react";

import "./styles.css";

import App from "./App";

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY ?? "";

function ConfigError() {
  return (
    <div className="screen-shell">
      <div className="status-card">
        <p className="eyebrow">Missing Config</p>
        <h1>Clerk publishable key not found</h1>
        <p>
          Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in the repo root <code>.env</code> before
          loading the web app.
        </p>
      </div>
    </div>
  );
}

const root = createRoot(document.getElementById("root")!);

if (!CLERK_PUBLISHABLE_KEY) {
  root.render(
    <StrictMode>
      <ConfigError />
    </StrictMode>,
  );
} else {
  root.render(
    <StrictMode>
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl="/">
        <App />
      </ClerkProvider>
    </StrictMode>,
  );
}
