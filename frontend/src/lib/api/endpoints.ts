/** Endpoint wrappers. One function per backend route the UI actually uses. */

import { apiFetch, buildQuery } from "./client";
import type {
  AdmissionCycle,
  DatasetMeta,
  HealthCheck,
  Page,
  PreferenceProfile,
  Profile,
  ProfileDetail,
  RecommendationItem,
  RecommendationItemPage,
  RecommendationRun,
  Scheme,
  SchemeVersionSummary,
  ScoreRankResult,
  RiskMode,
} from "./types";

export const metaApi = {
  cycles: () => apiFetch<AdmissionCycle[]>("/meta/admission-cycles"),
  dataset: (cycleId?: number) => apiFetch<DatasetMeta>(`/meta/dataset${buildQuery({ cycle_id: cycleId })}`),
  health: () => apiFetch<{ status: string; version: string; database: string }>("/meta/health"),
};

export interface ProfileCreateInput {
  cycle_id: number;
  display_name?: string | null;
  score?: number | null;
  rank?: number | null;
  subjects: string[];
  foreign_language?: string | null;
  sex?: string | null;
  subject_scores?: Array<{ subject_code: string; score: number | null }>;
  attributes?: Array<Record<string, unknown>>;
}

export const profileApi = {
  create: (input: ProfileCreateInput) => apiFetch<ProfileDetail>("/profiles", { method: "POST", body: input }),
  get: (id: number) => apiFetch<ProfileDetail>(`/profiles/${id}`),
  list: (limit = 20) => apiFetch<Page<Profile>>(`/profiles${buildQuery({ limit })}`),
  update: (id: number, body: Record<string, unknown>) =>
    apiFetch<ProfileDetail>(`/profiles/${id}`, { method: "PATCH", body }),
  setAttribute: (id: number, body: Record<string, unknown>) =>
    apiFetch<Record<string, unknown>>(`/profiles/${id}/attributes`, { method: "PUT", body }),
  preferences: (id: number) => apiFetch<PreferenceProfile[]>(`/profiles/${id}/preferences`),
  latestPreference: (id: number) => apiFetch<PreferenceProfile>(`/profiles/${id}/preferences/latest`),
  createPreference: (id: number, body: Record<string, unknown>) =>
    apiFetch<PreferenceProfile>(`/profiles/${id}/preferences`, { method: "POST", body }),
};

export const catalogApi = {
  scoreToRank: (cycleId: number, score: number) =>
    apiFetch<ScoreRankResult>("/catalog/score-to-rank", {
      method: "POST",
      body: { cycle_id: cycleId, score },
    }),
  universities: (params: { search?: string; limit?: number } = {}) =>
    apiFetch<Page<Record<string, unknown>>>(`/catalog/universities${buildQuery(params)}`),
  majors: (params: { search?: string; limit?: number } = {}) =>
    apiFetch<Page<Record<string, unknown>>>(`/catalog/majors${buildQuery(params)}`),
  /** Preference form options, derived from the loaded snapshot rather than hardcoded. */
  preferenceOptions: () =>
    apiFetch<{
      cycle_id: number | null;
      snapshot_code: string | null;
      provinces: Array<{ code: string; name: string; university_count: number; is_home: boolean }>;
      disciplines: Array<{ code: string; name: string; available: boolean }>;
      notes: Record<string, unknown>;
    }>("/catalog/preference-options"),
};

export const recommendationApi = {
  run: (body: {
    profile_id: number;
    preference_id?: number | null;
    cycle_id?: number | null;
    snapshot_id?: number | null;
    locked_plan_ids?: number[];
  }) => apiFetch<RecommendationRun>("/recommendations", { method: "POST", body }),
  session: (id: number) => apiFetch<RecommendationRun["session"]>(`/recommendations/${id}`),
  items: (
    sessionId: number,
    params: {
      limit?: number;
      offset?: number;
      risk_bucket?: string;
      eligibility?: string;
      search?: string;
      only_pooled?: boolean;
      sort?: string;
      province_name?: string;
      is_985?: boolean;
      is_211?: boolean;
      is_double_first_class?: boolean;
      /** 排除该方案当前版本已有的志愿（编辑器候选池用）。 */
      exclude_scheme_id?: number;
    } = {},
  ) => apiFetch<RecommendationItemPage>(`/recommendations/${sessionId}/items${buildQuery(params)}`),
  item: (itemId: number) => apiFetch<RecommendationItem>(`/recommendations/items/${itemId}`),
};

export const schemeApi = {
  create: (body: { recommendation_session_id: number; risk_mode?: RiskMode; target_count?: number; title?: string }) =>
    apiFetch<Scheme>("/schemes", { method: "POST", body }),
  get: (id: number, versionNo?: number) =>
    apiFetch<Scheme>(`/schemes/${id}${buildQuery({ version_no: versionNo })}`),
  versions: (id: number) => apiFetch<SchemeVersionSummary[]>(`/schemes/${id}/versions`),
  updateOrder: (id: number, body: { version: number; items: Array<{ recommendation_item_id: number; position?: number | null; locked?: boolean | null }>; change_summary?: string }) =>
    apiFetch<Scheme>(`/schemes/${id}/order`, { method: "PATCH", body }),
  addItems: (id: number, body: { version: number; recommendation_item_ids: number[] }) =>
    apiFetch<Scheme>(`/schemes/${id}/items`, { method: "POST", body }),
  removeItem: (id: number, position: number, version: number) =>
    apiFetch<Scheme>(`/schemes/${id}/items/${position}${buildQuery({ version })}`, { method: "DELETE" }),
  setLock: (id: number, position: number, version: number, locked: boolean) =>
    apiFetch<Scheme>(`/schemes/${id}/items/${position}/lock`, { method: "PUT", body: { version, locked } }),
  optimize: (id: number, body: { version: number; risk_mode?: RiskMode; target_count?: number; min_safe_count?: number | null }) =>
    apiFetch<Scheme>(`/schemes/${id}/optimize`, { method: "POST", body }),
  exportRows: (id: number, versionNo?: number) =>
    apiFetch<{ rows: Array<Record<string, unknown>>; disclaimer: string; application_limit: number | null }>(
      `/schemes/${id}/export${buildQuery({ version_no: versionNo })}`,
    ),
};

export const healthApi = {
  run: (schemeId: number, body: { version?: number | null; recheck_eligibility?: boolean; persist?: boolean } = {}) =>
    apiFetch<HealthCheck>(`/schemes/${schemeId}/health-check`, { method: "POST", body }),
  list: (schemeId: number) => apiFetch<HealthCheck[]>(`/schemes/${schemeId}/health-checks`),
  get: (checkId: number) => apiFetch<HealthCheck>(`/health-checks/${checkId}`),
};
