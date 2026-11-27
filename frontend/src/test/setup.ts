import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { clearMemoryStorage, getMemoryStorage } from "../lib/storage";

// Install an in-memory localStorage before any module accesses Node's experimental
// globalThis.localStorage getter (which warns when --localstorage-file is unset).
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  writable: true,
  value: getMemoryStorage(),
});
if (typeof window !== "undefined") {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    writable: true,
    value: getMemoryStorage(),
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  installFetchGuard();
  clearMemoryStorage();
});

// jsdom has no ResizeObserver; lightweight-charts and the chart shell use it.
class ResizeObserverMock {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!("ResizeObserver" in globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    writable: true,
    value: ResizeObserverMock,
  });
}

// Always provide matchMedia: jsdom may expose the property without a function,
// and lightweight-charts queries it for device-pixel-ratio changes.
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

if (!("scrollIntoView" in Element.prototype)) {
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: () => {},
  });
}

// No test may reach the network. A test that needs an API response replaces
// this mock explicitly.
function installFetchGuard(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("Unexpected network call in a test: mock fetch explicitly");
    }),
  );
}

installFetchGuard();
