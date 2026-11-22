import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeMode = "system" | "dark" | "light";
type Resolved = "dark" | "light";
const KEY = "dsl.theme";

const Ctx = createContext<{ mode: ThemeMode; resolved: Resolved; setMode: (m: ThemeMode) => void }>({
  mode: "system",
  resolved: "dark",
  setMode: () => undefined,
});

function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "light" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

const systemDark = () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readMode);
  const [sys, setSys] = useState<Resolved>(systemDark() ? "dark" : "light");
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const on = () => setSys(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const resolved: Resolved = mode === "system" ? sys : mode;
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", resolved === "dark" ? "#131722" : "#ffffff");
  }, [resolved]);
  const setMode = (m: ThemeMode) => {
    setModeState(m);
    try {
      localStorage.setItem(KEY, m);
    } catch {
      /* storage unavailable */
    }
  };
  return <Ctx.Provider value={{ mode, resolved, setMode }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
