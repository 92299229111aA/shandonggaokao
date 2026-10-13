/** Shared API types, mirroring the Pydantic DTOs. */

export type Eligibility = "PASS" | "FAIL" | "UNKNOWN";
export type RiskBucket = "HIGH_REACH" | "REACH" | "BORDER" | "STABLE" | "SAFE";
export type RiskMode = "STEADY" | "BALANCED" | "AGGRESSIVE";
export type ConfidenceLabel = "HIGH" | "MEDIUM" | "LOW";
export type Severity = "ERROR" | "WARNING" | "INFO";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdmissionCycle {
  id: number;
  province_code: string;
  exam_year: number;
  exam_category: string;
  batch_code: string;
  round_no: number;
  program_level: string;
  /** The volunteer cap. Always read from here — never hard-coded in the UI. */
  application_limit: number;
  filling_rule: string | null;
  policy_version: string;
  is_active: boolean;
}

export interface Snapshot {
  id: number;
  code: string;
  label: string;
  status: string;
  policy_version: string;
  is_demo: boolean;
  published_at: string | null;
}

export interface DatasetMeta {
  cycle: AdmissionCycle | null;
  snapshot: Snapshot | null;
  model_version: string | null;
  model_label: string | null;
  data_year_label: string | null;
  is_demo: boolean;
  plan_count: number;
  university_count: number;
  warnings: string[];
}

export interface SubjectScore {
  subject_code: string;
  score: number | null;
  grade: string | null;
}

export interface Attribute {
  id: number;
  attr_key: string;
  value_type: string;
  value_text: string | null;
  value_bool: boolean | null;
  value_int: number | null;
  source: string;
  note: string | null;
}

export interface Profile {
  id: number;
  display_name: string | null;
  cycle_id: number;
  exam_year: number;
  province_code: string;
  exam_category: string;
  score: number | null;
  rank: number | null;
  rank_source: string;
  subjects: string[];
  foreign_language: string | null;
  sex: string | null;
  status: string;
  version: number;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProfileDetail extends Profile {
  subject_scores: SubjectScore[];
  attributes: Attribute[];
}

export interface ScoreRankResult {
  exam_year: number;
  score: number;
  rank: number;
  segment_rank: number | null;
  same_score_count: number | null;
  ranked_candidates: number | null;
  source: "SEGMENT" | "INTERPOLATED";
  is_demo: boolean;
  note: string | null;
}

export interface MajorPreference {
  id: number;
  major_id: number | null;
  discipline_code: string | null;
  major_name: string | null;
  polarity: "INCLUDE" | "EXCLUDE";
  weight: number;
  is_hard_exclude: boolean;
  note: string | null;
}

export interface LocationPreference {
  id: number;
  province_code: string | null;
  province_name: string | null;
  city_code: string | null;
  city_name: string | null;
  polarity: "INCLUDE" | "EXCLUDE";
  weight: number;
  is_hard_exclude: boolean;
  note: string | null;
}

export interface UniversityPreference {
  id: number;
  university_id: number | null;
  trait: string | null;
  polarity: "INCLUDE" | "EXCLUDE";
  weight: number;
  is_hard_exclude: boolean;
  note: string | null;
}

export interface OtherPreference {
  id: number;
  dimension: "ACADEMIC" | "CAREER" | "TUITION" | "PERSONAL";
  key: string;
  value_text: string | null;
  value_num: number | null;
  polarity: "INCLUDE" | "EXCLUDE";
  weight: number;
  is_hard_exclude: boolean;
  note: string | null;
}

export interface PreferenceProfile {
  id: number;
  profile_id: number;
  version: number;
  risk_mode: RiskMode;
  candidate_rank: number | null;
  max_tuition: number | null;
  min_tuition: number | null;
  exclude_private: boolean;
  exclude_cooperative: boolean;
  accept_unknown_requirements: boolean;
  target_count: number;
  min_safe_count: number | null;
  dimension_weights: Record<string, number> | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  major_preferences: MajorPreference[];
  location_preferences: LocationPreference[];
  university_preferences: UniversityPreference[];
  other_preferences: OtherPreference[];
}

export interface RecommendationSession {
  id: number;
  profile_id: number;
  preference_profile_id: number;
  cycle_id: number;
  snapshot_id: number;
  model_version_id: number;
  profile_version: number;
  preference_version: number;
  policy_version: string;
  model_version: string;
  algorithm_config_version: string | null;
  status: string;
  stage: string | null;
  stats: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface HistoryYear {
  exam_year: number;
  min_rank: number | null;
  min_score: number | null;
  plan_count: number | null;
  admitted_count: number | null;
}

export interface RecommendationItem {
  item_id: number;
  plan_id: number;
  program_label: string;
  plan_name: string;
  university_id: number;
  university_name: string;
  province_name: string | null;
  city_name: string | null;
  is_985: boolean;
  is_211: boolean;
  is_double_first_class: boolean;
  eligibility: Eligibility;
  eligibility_reasons: Array<Record<string, unknown>>;
  excluded_by: string | null;
  acs: number | null;
  base_acs: number | null;
  confidence: number | null;
  confidence_label: ConfidenceLabel | null;
  risk_bucket: RiskBucket | null;
  acs_explanation: Record<string, unknown> | null;
  preference_score: number | null;
  preference_dimensions: Record<string, unknown> | null;
  history: HistoryYear[];
  plan_count: number;
  plan_count_history: number[];
  plan_change_ratio: number | null;
  median_plan_history: number | null;
  tuition_fee: number;
  duration_years: number;
  campus_name: string | null;
  subject_requirement_text: string | null;
  special_requirements: string | null;
  requirement_notes: string[];
  cooperative: string;
  is_demo: boolean;
  in_candidate_pool: boolean;
}

export interface RecommendationItemPage {
  items: RecommendationItem[];
  total: number;
  limit: number;
  offset: number;
  risk_distribution: Record<string, number>;
  pool_size: number;
}

export interface RecommendationRun {
  session: RecommendationSession;
  pool_size: number;
  risk_distribution: Record<string, number>;
  shortfall_notice: string | null;
  relax_suggestions: string[];
  /** Remarks about the inputs themselves, e.g. a 位次 beyond the 一段 population. */
  input_warnings?: string[];
}

export interface SchemeItem {
  position: number;
  recommendation_item_id: number;
  plan_id: number;
  program_label: string;
  plan_name: string;
  university_name: string;
  eligibility: Eligibility;
  acs: number | null;
  confidence: number | null;
  preference_score: number | null;
  desire_band: number | null;
  risk_bucket: RiskBucket | null;
  locked: boolean;
  fixed_position: boolean;
  tuition_fee: number;
  plan_count: number;
  is_demo: boolean;
}

export interface Scheme {
  id: number;
  session_id: number;
  title: string;
  target_count: number;
  risk_mode: RiskMode;
  current_version_no: number;
  lock_version: number;
  is_demo: boolean;
  version_no: number;
  operation: string;
  change_summary: string | null;
  item_count: number;
  optimizer_stats: Record<string, unknown> | null;
  risk_distribution: Record<string, number>;
  items: SchemeItem[];
  is_historical: boolean;
}

export interface SchemeVersionSummary {
  id: number;
  scheme_id: number;
  version_no: number;
  operation: string;
  change_summary: string | null;
  item_count: number;
  model_version: string | null;
  optimizer_stats: Record<string, unknown> | null;
  created_at: string;
}

export interface HealthIssue {
  code: string;
  severity: Severity;
  message: string;
  position: number | null;
  detail: Record<string, unknown>;
  suggestion: string | null;
}

export interface HealthCheck {
  id: number | null;
  scheme_id: number;
  scheme_version_id: number | null;
  version_no: number | null;
  snapshot_id: number | null;
  score: number;
  error_count: number;
  warning_count: number;
  info_count: number;
  passed: boolean;
  disclaimer: string;
  summary: Record<string, unknown>;
  issues: HealthIssue[];
  checks: Array<{ code: string; title: string; passed: boolean; errors: number; warnings: number; infos: number }>;
}
