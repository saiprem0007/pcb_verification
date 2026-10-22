// Mirrors backend/app/schemas — keep in sync with the Pydantic models.

export type InspectionStatus = "GOOD" | "DEFECTIVE" | "REVIEW";

export type DefectType =
  | "MISSING_COMPONENT"
  | "SHIFTED_COMPONENT"
  | "ROTATED_COMPONENT"
  | "SIZE_DEVIATION"
  | "VISUAL_ANOMALY"
  | "WRONG_COMPONENT"
  | "UNEXPECTED_COMPONENT";

export type ModelStatus =
  | "DETERMINISTIC_ONLY"
  | "MODEL NOT CONFIGURED"
  | "YOLO_ACTIVE";

export interface Defect {
  component: string;
  type: DefectType;
  confidence: number;
  expected: string;
  observed: string;
  expected_position: [number, number] | null;
  actual_position: [number, number] | null;
  deviation_px: number | null;
  deviation_mm: number | null;
  allowed_px: number | null;
  rotation_deg: number | null;
  allowed_rotation_deg: number | null;
  bbox: [number, number, number, number] | null;
  // Phase 1: severity annotation (populated by backend, always present after Phase 1)
  severity?: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | null;
  corrective_action?: string;
  // Phase 4B (all optional; absent for older results)
  certainty?: "confirmed" | "suspected" | null;
  signals?: string[];
  stage_id?: string | null;
  expected_class?: string | null;
  observed_class?: string | null;
  expected_rotation_deg?: number | null;
  observed_rotation_deg?: number | null;
  expected_size_px?: [number, number] | null;
  observed_size_px?: [number, number] | null;
  expected_position_mm?: [number, number] | null;
  observed_position_mm?: [number, number] | null;
}

export type ReasonCode =
  | "OK"
  | "INVALID_IMAGE"
  | "POOR_IMAGE_QUALITY"
  | "ALIGNMENT_FAILED"
  | "CRITICAL_DEFECT"
  | "VISUAL_ANOMALY"
  | "LOW_CONFIDENCE"
  | "CONTRADICTORY"
  | "SUSPECTED_DEFECT"
  | "AMBIGUOUS_ORIENTATION";

export interface ReviewReason {
  code: ReasonCode;
  component?: string | null;
  detail: string;
}

export interface QualityReport {
  passed: boolean;
  blur_score: number;
  brightness: number;
  contrast: number;
  overexposed_frac: number;
  issues: string[];
}

export interface BoardDetection {
  found: boolean;
  method: string;
  coverage: number;
  quad: [number, number][];
  note: string;
}

export interface AlignmentMetadata {
  success: boolean;
  rotation_deg: number;
  scale: number;
  translation_px: [number, number];
  inliers: number;
  alignment_error_px: number;
  reason: string;
}

export interface RegionMeasurement {
  name: string;
  found: boolean;
  peak_corr: number;
  offset_px: number;
  rotation_deg: number;
  ssim: number;
  expected_center: [number, number];
  observed_center: [number, number] | null;
  bbox: [number, number, number, number];
  detector_class: string | null;
  detector_confidence: number | null;
  detector_agreement: "agree" | "conflict" | "absent" | null;
}

export interface ComponentDetection {
  label: string;
  confidence: number;
  bbox: [number, number, number, number];
  matched_region: string | null;
}

export type AnyResult = InspectionResult | MultiViewResult;

export function isMulti(r: AnyResult): r is MultiViewResult {
  return r.result_type === "multi";
}

export interface ViewResult {
  name: string;
  source: string;
  result: InspectionResult;
}

export interface MultiViewResult {
  result_type: "multi";
  inspection_id: string;
  timestamp: string;
  reference_id: string;
  profile_kind: string;
  status: InspectionStatus;
  reason: string;
  explanation: string;
  overall_confidence: number;
  inspection_time_ms: number;
  view_count: number;
  defects: Defect[];
  views: ViewResult[];
  montage_image_path: string;
  model_status: ModelStatus;
}

export interface InspectionResult {
  result_type: string;
  inspection_id: string;
  timestamp: string;
  reference_id: string;
  profile_kind: string;
  status: InspectionStatus;
  reason: string;
  explanation: string;
  overall_confidence: number;
  inspection_time_ms: number;
  quality: QualityReport;
  detection: BoardDetection;
  alignment: AlignmentMetadata;
  board_similarity: number;
  regions: RegionMeasurement[];
  detections: ComponentDetection[];
  defects: Defect[];
  original_image_path: string;
  cropped_image_path: string;
  aligned_image_path: string;
  annotated_image_path: string;
  montage_image_path: string;
  model_status: ModelStatus;
  // Phase 1: stage / unit identity (optional — null for legacy / stage-unaware runs)
  stage_id?: string | null;
  stage_name?: string | null;
  unit_id?: string | null;
  // Phase 4B
  review_reasons?: ReviewReason[];
  mm_scale_source?: "transform" | "scalar" | "config" | "none" | null;
  mm_per_px?: number | null;
  warnings?: string[];
}

export interface Region {
  name: string;
  bbox: [number, number, number, number];
  expected_class: string | null;
  // Phase 4B: design rotation, CCW-positive as seen on the reference image
  expected_rotation_deg?: number | null;
  tolerances: Record<string, unknown>;
}

export interface Reference {
  id: string;
  name: string;
  kind: string;
  inspection_mode: "regions" | "surface";
  active: boolean;
  image_path: string;
  regions: Region[];
  mm_per_px: number | null;
  created_at: string;
  notes: string;
  // Phase 1: optional stage metadata (null for legacy / single-stage references)
  product_id?: string | null;
  stage_id?: string | null;
  stage_name?: string | null;
  stage_order?: number | null;
  // Phase 3: affine transform matrix [[a,b,c],[d,e,f]] from design-mm → ref-px; null when no placement file has been imported
  placement_transform?: number[][] | null;
}

export interface HistoryRow {
  inspection_id: string;
  timestamp: string;
  reference_id: string;
  profile_kind: string;
  status: InspectionStatus;
  reason: string;
  overall_confidence: number;
  inspection_time_ms: number;
  defect_count: number;
  // Phase 1: stage / unit (null for old rows)
  stage_id?: string | null;
  unit_id?: string | null;
}

export interface StationState {
  enabled: boolean;
  counter: number;
  view_count: number;
  verdict: "GOOD" | "DEFECTIVE" | "REVIEW" | "IDLE";
  reason: string;
  defect_count: number;
  reject: boolean;
  inspection_id: string;
  timestamp: string;
  source: string;
  esp32_online: boolean;
  esp32_last_seen_s_ago: number | null;
}

export interface Stats {
  total: number;
  avg_time_ms: number;
  good: number;
  defective: number;
  review: number;
}

// Phase 1: stage-aware reference browsing
export interface StageRef {
  reference_id: string;
  stage_id?: string | null;
  stage_name?: string | null;
  stage_order?: number | null;
}

export interface ProductStages {
  product_id: string;
  stages: StageRef[];
}
