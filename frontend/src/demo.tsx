import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

// Outside a DemoProvider (a screen under test) ApplAI is not the demo.
const DemoContext = createContext(false);

/** Whether ApplAI runs as the public demo, where nothing is sent. */
export function useDemo(): boolean {
  return useContext(DemoContext);
}

/** Asks /api/health once. A health check that fails means no demo: the API refuses a send in the demo anyway. */
export function DemoProvider({ children }: { children: ReactNode }) {
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    let current = true;
    api.health().then(
      (health) => current && setDemo(health.demo),
      () => {},
    );
    return () => {
      current = false;
    };
  }, []);
  return <DemoContext.Provider value={demo}>{children}</DemoContext.Provider>;
}
