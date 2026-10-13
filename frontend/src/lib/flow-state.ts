"use client";

/**
 * The onboarding journey is linear across pages, so the identifiers are kept in
 * localStorage instead of building an auth/session system V1 does not need.
 */

import { useCallback, useEffect, useState } from "react";

const KEY = "sdgk.flow.v1";

export interface FlowState {
  cycleId: number | null;
  profileId: number | null;
  preferenceId: number | null;
  sessionId: number | null;
  schemeId: number | null;
  /** Warnings about the *inputs* from the last run (e.g. 位次 beyond the 一段 range).
   *  Carried across the navigation to /recommendations so they are actually seen. */
  inputWarnings: string[];
}

const EMPTY: FlowState = {
  cycleId: null,
  profileId: null,
  preferenceId: null,
  sessionId: null,
  schemeId: null,
  inputWarnings: [],
};

export function useFlowState() {
  const [state, setState] = useState<FlowState>(EMPTY);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (raw) setState({ ...EMPTY, ...(JSON.parse(raw) as Partial<FlowState>) });
    } catch {
      // a corrupt entry must not break the flow: fall back to empty
    }
    setLoaded(true);
  }, []);

  const update = useCallback((patch: Partial<FlowState>) => {
    setState((previous) => {
      const next = { ...previous, ...patch };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        // storage may be unavailable (private mode); the flow still works in-memory
      }
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    try {
      window.localStorage.removeItem(KEY);
    } catch {
      // ignore
    }
    setState(EMPTY);
  }, []);

  return { state, update, reset, loaded };
}
