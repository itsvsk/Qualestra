export type RunStatus =
  | 'queued'
  | 'preparing'
  | 'exploring'
  | 'analyzing'
  | 'verifying'
  | 'reporting'
  | 'gating'
  | 'passed'
  | 'warned'
  | 'blocked'
  | 'cancelled'
  | 'failed'

export interface RunBudget {
  max_minutes: number
  max_actions: number
  max_llm_cost_usd: number
  max_api_requests: number
}

export interface RunRequest {
  target_id: string
  trigger: 'manual' | 'pull_request' | 'scheduled' | 'verification'
  commit_sha?: string | null
  changed_files: string[]
  changed_operations?: string[]
  coverage_gaps?: string[]
  personas: string[]
  browsers: string[]
  browser_settings?: BrowserSettings
  budget: RunBudget
  policy_profile: string
  seed: number
}

export interface BrowserSettings {
  viewport_width: number
  viewport_height: number
  locale: string
  timezone_id: string
  color_scheme: 'light' | 'dark' | 'no-preference'
  reduced_motion: 'reduce' | 'no-preference'
}

export interface CreateRunRequest {
  target_id: string
  trigger?: RunRequest['trigger']
  commit_sha?: string
  personas?: string[]
  browsers?: string[]
  browser_settings?: BrowserSettings
  policy_profile?: string
  seed: number
  budget?: RunBudget
  changed_files?: string[]
  changed_operations?: string[]
  coverage_gaps?: string[]
}

export interface Run {
  id: string
  status: RunStatus
  request: RunRequest
  started_at?: string | null
  ended_at?: string | null
  cancellation_requested: boolean
}

export interface AgentTask {
  task_id: string
  agent_type: string
  status: string
  target_partition: string
  dependency_task_ids: string[]
  budget: {
    max_seconds: number
    max_tool_calls: number
    max_model_tokens: number
    max_api_requests: number
    max_artifact_bytes: number
  }
  attempt: number
}

export interface EventItem {
  event_id: string
  event_type: string
  occurred_at: string
  producer: string
  payload: Record<string, unknown>
}

export interface CoverageNode {
  id: string
  route: string
  role: string
  visited_count: number
}

export interface CoverageEdge {
  from_state_id: string
  to_state_id: string
  outcome: string
}

export interface Coverage {
  states: number
  transitions: number
  routes: number
  roles: number
  graph: { nodes: CoverageNode[]; edges: CoverageEdge[] }
}

export interface DefectEvidence {
  kind: string
  artifact_id: string
}

export interface Defect {
  defect_id: string
  run_id: string
  title: string
  severity: string
  confidence: number
  status: 'verified'
  affected_area: string
  signature: string
  persona: string
  preconditions: string[]
  steps_to_reproduce: Record<string, unknown>[]
  expected: string
  actual: string
  verification: { attempts: number; matched: number }
  evidence: DefectEvidence[]
  suspected_component: string
  suggested_investigation: string
  created_at: string
}

export interface GateRule {
  metric: string
  operator: string
  threshold: unknown
  actual: unknown
  matched: boolean
  level: string
}

export interface Gate {
  outcome: string
  release_confidence: number
  blockers: string[]
  rules: GateRule[]
  metrics: Record<string, unknown>
  generated_at: string
}

export interface RunDetails {
  tasks: AgentTask[]
  events: EventItem[]
  coverage?: Coverage
  defects: Defect[]
  gate?: Gate
}

export type ReportFormat = 'markdown' | 'html' | 'json'

export interface ReportDocument {
  report_id: string
  format: ReportFormat
  media_type: string
  content: string
  digest: string
}

export interface RunReport {
  run_id: string
  schema_version: string
  generated_at: string
  title: string
  summary: string
  gate_outcome: string
  verified_defect_count: number
  documents: ReportDocument[]
}

export type DeliveryMode = 'dry_run' | 'manual' | 'automatic'
export type DeliveryKind = 'artifact' | 'ticket' | 'alert'
export type DeliveryStatus =
  | 'draft'
  | 'pending_approval'
  | 'queued'
  | 'processing'
  | 'succeeded'
  | 'failed'

export interface DeliveryAttempt {
  attempt: number
  status: string
  recorded_at: string
  error?: string | null
}

export interface Delivery {
  delivery_id: string
  run_id: string
  report_id: string
  connector_id: string
  destination_id: string
  mode: DeliveryMode
  status: DeliveryStatus
  created_at: string
  updated_at: string
  attempts: DeliveryAttempt[]
  external_reference?: string | null
  external_url?: string | null
}

export interface CreateDeliveryRequest {
  report_id: string
  connector_id: string
  destination_id: string
  mode: DeliveryMode
}

export interface DeliveryDestination {
  connector_id: string
  destination_id: string
  display_name: string
  kind: DeliveryKind
  provider: string
  supported_modes: DeliveryMode[]
}
