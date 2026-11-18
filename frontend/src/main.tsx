import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { MetaProvider } from "./state/meta";
import { ThemeProvider } from "./state/theme";
import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Missing #root element");

createRoot(rootElement).render(
  <StrictMode>
    <ThemeProvider>
      <MetaProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </MetaProvider>
    </ThemeProvider>
  </StrictMode>,
);
