/**
 * localStorage can be absent (SSR, restricted storage, some test runtimes).
 * Fall back to an in-memory Storage so preferences still work for the session.
 */
const memory = new Map<string, string>();

const memoryStorage: Storage = {
  get length() {
    return memory.size;
  },
  clear() {
    memory.clear();
  },
  getItem(key: string) {
    return memory.has(key) ? (memory.get(key) as string) : null;
  },
  key(index: number) {
    return Array.from(memory.keys())[index] ?? null;
  },
  removeItem(key: string) {
    memory.delete(key);
  },
  setItem(key: string, value: string) {
    memory.set(key, String(value));
  },
};

export function getStorage(): Storage {
  if (typeof window !== "undefined") {
    try {
      const storage = window.localStorage;
      if (storage && typeof storage.getItem === "function") return storage;
    } catch {
      // Fall through to memory.
    }
  }
  return memoryStorage;
}

export function getMemoryStorage(): Storage {
  return memoryStorage;
}

export function clearMemoryStorage(): void {
  memory.clear();
}
