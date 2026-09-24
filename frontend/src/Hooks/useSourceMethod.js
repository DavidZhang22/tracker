import { useEffect, useState } from "react";
import { post } from "../api";
import { apiMethods } from "../Components/SourceMethod";

const knownMethods = new Set(["auto", ...apiMethods.map(([id]) => id)]);

export default function useSourceMethod(url, defaultMethod, selector, busy) {
  // Null means URL detection owns the choice. An explicit Automatic choice
  // opts out, as do saved non-Automatic defaults and custom CSS selectors.
  const [choice, setChoice] = useState(
    defaultMethod && defaultMethod !== "auto" ? defaultMethod : null,
  );
  const [detected, setDetected] = useState(null);
  const enabled = choice === null && !selector.trim();
  useEffect(() => {
    if (!enabled || busy || !/^https?:\/\/[^\s/]+/i.test(url)) return;
    let active = true;
    const timer = setTimeout(() => {
      post("/source-method/detect", { url })
        .then((result) => {
          if (active && knownMethods.has(result?.source_method))
            setDetected({ ...result, url });
        })
        .catch(() => {}); // The scan resolves the method again if detection fails.
    }, 350);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [url, enabled, busy]);
  const match = enabled && detected?.url === url ? detected : null;
  const method = choice || match?.source_method || "auto";
  return {
    method,
    sourceStatus: match?.source_status || null,
    select: setChoice,
    note:
      match?.note ||
      (match && method !== "auto"
        ? "Selected from the URL. You can change it."
        : ""),
    // Resolve server-side even if Scan is clicked before the debounce completes.
    request: { source_method: choice || "auto", detect_api: enabled },
    accept: (result) => {
      if (enabled && knownMethods.has(result.source_method))
        setDetected({ url, source_method: result.source_method, note: "" });
    },
  };
}
