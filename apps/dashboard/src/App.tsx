import { FormEvent, useEffect, useMemo, useState } from 'react'
import { ApiError, api, artifactHref } from './api'
import { ReportCentre } from './ReportCentre'
import type {
  AgentTask,
  Coverage,
  CreateRunRequest,
  Defect,
  EventItem,
  Gate,
  Run,
  RunDetails,
} from './types'
import './styles.css'

const TERMINAL = new Set(['passed', 'warned', 'blocked', 'cancelled', 'failed'])
const EMPTY_DETAILS: RunDetails = { tasks: [], events: [], defects: [] }

function short(value: string) {
  return value.slice(0, 8)
}

function display(value: unknown) {
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function relativeTime(value?: string | null) {
  if (!value) return 'not started'
  const milliseconds = Date.now() - new Date(value).getTime()
  if (!Number.isFinite(milliseconds)) return 'unknown time'
  const minutes = Math.floor(milliseconds / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`
}

function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value.toLowerCase()}`}>
      <i />
      {value}
    </span>
  )
}

interface NewRunFormProps {
  busy: boolean
  onSubmit: (request: CreateRunRequest) => Promise<void>
  onClose: () => void
}

function NewRunForm({ busy, onSubmit, onClose }: NewRunFormProps) {
  const [target, setTarget] = useState('atlas-local')
  const [seed, setSeed] = useState('41021')
  const [personas, setPersonas] = useState('owner, viewer')
  const [browsers, setBrowsers] = useState('chromium')
  const [locale, setLocale] = useState('en-US')
  const [viewportWidth, setViewportWidth] = useState('1440')
  const [viewportHeight, setViewportHeight] = useState('900')
  const [commit, setCommit] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await onSubmit({
        target_id: target.trim(),
        seed: Number(seed),
        trigger: 'manual',
        commit_sha: commit.trim() || undefined,
        personas: personas
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        browsers: browsers
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        browser_settings: {
          viewport_width: Number(viewportWidth),
          viewport_height: Number(viewportHeight),
          locale: locale.trim(),
          timezone_id: 'UTC',
          color_scheme: 'light',
          reduced_motion: 'no-preference',
        },
      })
    } catch {
      // The parent displays the sanitized API error and keeps this form open for correction.
    }
  }

  return (
    <form className="new-run" onSubmit={(event) => void submit(event)}>
      <div className="form-heading">
        <b>Start quality run</b>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close new run form">
          ×
        </button>
      </div>
      <label>
        Target ID
        <input required value={target} onChange={(event) => setTarget(event.target.value)} />
      </label>
      <div className="form-row">
        <label>
          Deterministic seed
          <input
            required
            type="number"
            min="0"
            max="2147483647"
            value={seed}
            onChange={(event) => setSeed(event.target.value)}
          />
        </label>
        <label>
          Commit (optional)
          <input value={commit} onChange={(event) => setCommit(event.target.value)} placeholder="abc123" />
        </label>
      </div>
      <label>
        Personas
        <input required value={personas} onChange={(event) => setPersonas(event.target.value)} />
      </label>
      <div className="form-row">
        <label>
          Browser matrix
          <input
            required
            value={browsers}
            onChange={(event) => setBrowsers(event.target.value)}
            placeholder="chromium, firefox, webkit"
          />
        </label>
        <label>
          Locale
          <input required value={locale} onChange={(event) => setLocale(event.target.value)} />
        </label>
      </div>
      <div className="form-row">
        <label>
          Viewport width
          <input
            required
            type="number"
            min="320"
            max="3840"
            value={viewportWidth}
            onChange={(event) => setViewportWidth(event.target.value)}
          />
        </label>
        <label>
          Viewport height
          <input
            required
            type="number"
            min="320"
            max="2160"
            value={viewportHeight}
            onChange={(event) => setViewportHeight(event.target.value)}
          />
        </label>
      </div>
      <p>Actions remain constrained by the selected target policy.</p>
      <button className="primary wide" disabled={busy || !target.trim()}>
        {busy ? 'Starting…' : 'Start run'}
      </button>
    </form>
  )
}

interface RunListProps {
  runs: Run[]
  selected?: string
  onSelect: (id: string) => void
  onCreate: (request: CreateRunRequest) => Promise<void>
  creating: boolean
}

function RunList({ runs, selected, onSelect, onCreate, creating }: RunListProps) {
  const [showNew, setShowNew] = useState(false)
  return (
    <aside className="rail">
      <div className="brand">
        <div className="mark">S</div>
        <div>
          <strong>Qualestra</strong>
          <small>CONTROL ROOM</small>
        </div>
      </div>
      <button className="primary wide new-run-button" onClick={() => setShowNew(true)}>
        + New run
      </button>
      {showNew && (
        <NewRunForm
          busy={creating}
          onSubmit={async (request) => {
            await onCreate(request)
            setShowNew(false)
          }}
          onClose={() => setShowNew(false)}
        />
      )}
      <div className="rail-title">
        RUNS <span>{runs.length}</span>
      </div>
      <nav aria-label="Quality runs">
        {runs.map((run) => (
          <button
            key={run.id}
            className={selected === run.id ? 'run active' : 'run'}
            onClick={() => onSelect(run.id)}
          >
            <span>
              <b>{run.request.target_id}</b>
              <small>
                #{short(run.id)} · {relativeTime(run.started_at)}
              </small>
            </span>
            <Status value={run.status} />
          </button>
        ))}
        {!runs.length && <p className="empty rail-empty">No runs yet. Start the first quality check.</p>}
      </nav>
      <div className="boundary">
        <i /> AI proposes
        <br />
        <i /> Policy permits
        <br />
        <i /> Evidence decides
      </div>
    </aside>
  )
}

function TaskTable({ tasks }: { tasks: AgentTask[] }) {
  return (
    <section className="panel span-2">
      <header>
        <div>
          <small>ORCHESTRATION</small>
          <h2>Logical agent task DAG</h2>
        </div>
        <span className="count">{tasks.length} TASKS</span>
      </header>
      <div className="table-scroll">
        <div className="table">
          <div className="tr th">
            <span>Agent</span><span>Partition</span><span>Budget</span><span>Attempt</span><span>State</span>
          </div>
          {tasks.map((task) => (
            <div className="tr" key={task.task_id}>
              <span><b>{task.agent_type}</b><small>#{short(task.task_id)} · deps {task.dependency_task_ids.length}</small></span>
              <span className="mono">{task.target_partition}</span>
              <span>{task.budget.max_seconds}s / {task.budget.max_tool_calls} calls</span>
              <span>{task.attempt}</span>
              <span><Status value={task.status} /></span>
            </div>
          ))}
          {!tasks.length && <p className="empty table-empty">No tasks planned yet.</p>}
        </div>
      </div>
    </section>
  )
}

function Timeline({ events }: { events: EventItem[] }) {
  return (
    <section className="panel">
      <header><div><small>AUDIT TRAIL</small><h2>Run timeline</h2></div><span className="count">{events.length} EVENTS</span></header>
      <div className="timeline">
        {!events.length && <p className="empty">No events committed yet.</p>}
        {[...events].reverse().map((event) => (
          <details className="event" key={event.event_id}>
            <summary><i /><span><b>{event.event_type}</b><small>{event.producer} · {new Date(event.occurred_at).toLocaleTimeString()}</small></span></summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </details>
        ))}
      </div>
    </section>
  )
}

function CoverageGraph({ coverage }: { coverage?: Coverage }) {
  const nodes = useMemo(() => coverage?.graph.nodes ?? [], [coverage])
  const edges = coverage?.graph.edges ?? []
  const positions = useMemo(() => {
    const centerX = 360
    const centerY = 170
    const radiusX = 265
    const radiusY = 110
    return new Map(nodes.map((node, index) => {
      const angle = (index * Math.PI * 2) / Math.max(nodes.length, 1) - Math.PI / 2
      return [node.id, { x: centerX + Math.cos(angle) * radiusX, y: centerY + Math.sin(angle) * radiusY }]
    }))
  }, [nodes])

  return (
    <section className="panel coverage-panel">
      <header>
        <div><small>PRODUCT MODEL</small><h2>Observed state graph</h2></div>
        <span className="count">{edges.length} EDGES</span>
      </header>
      <div className="coverage-summary">
        <b>{coverage?.states ?? 0}</b><span>states</span>
        <b>{coverage?.routes ?? 0}</b><span>routes</span>
        <b>{coverage?.roles ?? 0}</b><span>roles</span>
        <b>{coverage?.transitions ?? 0}</b><span>transitions</span>
      </div>
      {nodes.length ? (
        <div className="graph-wrap">
          <svg viewBox="0 0 720 340" role="img" aria-label={`${nodes.length} observed states connected by ${edges.length} transitions`}>
            <defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" /></marker></defs>
            {edges.map((edge, index) => {
              const from = positions.get(edge.from_state_id)
              const to = positions.get(edge.to_state_id)
              if (!from || !to) return null
              return <line key={`${edge.from_state_id}-${edge.to_state_id}-${index}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y} markerEnd="url(#arrow)"><title>{edge.outcome}</title></line>
            })}
            {nodes.map((node) => {
              const position = positions.get(node.id)
              if (!position) return null
              return <g className="graph-node" key={node.id} transform={`translate(${position.x},${position.y})`}><circle r="29" /><text y="-3">{node.role}</text><text className="route-label" y="12">{node.route}</text><title>{node.route} · {node.role} · visited {node.visited_count} times</title></g>
            })}
          </svg>
          <div className="transition-list">
            {edges.map((edge, index) => <span key={`${edge.from_state_id}-${index}`}><i />{short(edge.from_state_id)} → {short(edge.to_state_id)} <em>{edge.outcome}</em></span>)}
            {!edges.length && <span>No transitions observed yet.</span>}
          </div>
        </div>
      ) : <p className="empty panel-empty">No states observed yet.</p>}
    </section>
  )
}

function EvidenceLinks({ defect }: { defect: Defect }) {
  return (
    <div className="evidence-links">
      {defect.evidence.map((evidence, index) => {
        const href = artifactHref(evidence.artifact_id)
        return href ? (
          <a key={`${evidence.artifact_id}-${index}`} href={href} target="_blank" rel="noreferrer" download>
            <span>↗</span> {evidence.kind || 'evidence'} {index + 1}
          </a>
        ) : <span className="unavailable" key={`${evidence.artifact_id}-${index}`}>Evidence {index + 1} unavailable</span>
      })}
    </div>
  )
}

function DefectList({ defects }: { defects: Defect[] }) {
  return (
    <section className="panel defects-panel">
      <header><div><small>VERIFIED FINDINGS</small><h2>Defects and evidence</h2></div><span className="count">{defects.length} VERIFIED</span></header>
      <div className="defects">
        {defects.map((defect) => (
          <details key={defect.defect_id}>
            <summary>
              <Status value={defect.severity} />
              <span><b>{defect.title}</b><small>{defect.affected_area} · confidence {(defect.confidence * 100).toFixed(0)}% · reproduced {defect.verification.matched}/{defect.verification.attempts}</small></span>
              <i>⌄</i>
            </summary>
            <div className="defect-detail">
              <dl><div><dt>Expected</dt><dd>{defect.expected}</dd></div><div><dt>Actual</dt><dd>{defect.actual}</dd></div><div><dt>Component</dt><dd>{defect.suspected_component}</dd></div><div><dt>Persona</dt><dd>{defect.persona}</dd></div></dl>
              <h3>Minimal reproduction</h3>
              <ol>{defect.steps_to_reproduce.map((step, index) => <li key={index}><code>{display(step)}</code></li>)}</ol>
              <p className="investigation"><b>Suggested investigation:</b> {defect.suggested_investigation}</p>
              <EvidenceLinks defect={defect} />
            </div>
          </details>
        ))}
        {!defects.length && <p className="empty">No verified defects.</p>}
      </div>
    </section>
  )
}

function GatePanel({ gate }: { gate?: Gate }) {
  return (
    <section className="panel span-2 gate-panel">
      <header>
        <div><small>DETERMINISTIC DECISION</small><h2>Quality gate operands</h2></div>
        {gate && <div className="gate-result"><span>{Math.round(gate.release_confidence * 100)}% confidence</span><Status value={gate.outcome} /></div>}
      </header>
      {gate ? (
        <>
          {!!gate.blockers.length && <div className="blockers"><b>Release blockers</b>{gate.blockers.map((blocker) => <span key={blocker}>{blocker}</span>)}</div>}
          <div className="gate-rules">
            {gate.rules.map((rule) => (
              <div className="gate-rule" key={`${rule.level}-${rule.metric}`}>
                <i className={rule.matched ? 'matched' : 'clear'} />
                <span><b>{rule.metric.replaceAll('_', ' ')}</b><small>actual <strong>{display(rule.actual)}</strong> {rule.operator} threshold <strong>{display(rule.threshold)}</strong></small></span>
                <em>{rule.matched ? `${rule.level} matched` : 'clear'}</em>
              </div>
            ))}
          </div>
          <details className="metrics-detail"><summary>All calculated metrics ({Object.keys(gate.metrics).length})</summary><div>{Object.entries(gate.metrics).map(([name, value]) => <span key={name}><b>{name.replaceAll('_', ' ')}</b><code>{display(value)}</code></span>)}</div></details>
        </>
      ) : <p className="empty panel-empty">Gate is pending terminal evidence.</p>}
    </section>
  )
}

interface ComparisonProps {
  run: Run
  runs: Run[]
  details: RunDetails
}

function Comparison({ run, runs, details }: ComparisonProps) {
  const [baselineId, setBaselineId] = useState('')
  const [baseline, setBaseline] = useState<RunDetails>()
  const [loading, setLoading] = useState(false)
  const candidates = runs.filter((item) => item.id !== run.id && item.request.target_id === run.request.target_id)

  useEffect(() => {
    if (!baselineId) return
    let current = true
    async function load() {
      setLoading(true)
      const [coverage, defects, gate] = await Promise.allSettled([
        api.coverage(baselineId), api.defects(baselineId), api.gate(baselineId),
      ])
        if (!current) return
        setBaseline({
          tasks: [], events: [],
          coverage: coverage.status === 'fulfilled' ? coverage.value : undefined,
          defects: defects.status === 'fulfilled' ? defects.value : [],
          gate: gate.status === 'fulfilled' ? gate.value : undefined,
        })
        setLoading(false)
    }
    void load()
    return () => { current = false }
  }, [baselineId])

  const comparison = [
    ['Verified defects', details.defects.length, baseline?.defects.length],
    ['Observed states', details.coverage?.states ?? 0, baseline?.coverage?.states],
    ['Transitions', details.coverage?.transitions ?? 0, baseline?.coverage?.transitions],
    ['Gate confidence', Math.round((details.gate?.release_confidence ?? 0) * 100), baseline?.gate ? Math.round(baseline.gate.release_confidence * 100) : undefined],
  ] as const

  return (
    <section className="panel span-2 comparison-panel">
      <header><div><small>REGRESSION VIEW</small><h2>Compare runs</h2></div>
        <label>Baseline<select aria-label="Comparison baseline" value={baselineId} onChange={(event) => { setBaseline(undefined); setLoading(false); setBaselineId(event.target.value) }}><option value="">Select a previous run</option>{candidates.map((candidate) => <option value={candidate.id} key={candidate.id}>#{short(candidate.id)} · {candidate.status} · seed {candidate.request.seed}</option>)}</select></label>
      </header>
      {loading && <p className="empty panel-empty">Loading comparison…</p>}
      {!loading && baseline && <div className="comparison-grid"><div className="comparison-head"><span>Metric</span><span>Current</span><span>Baseline</span><span>Change</span></div>{comparison.map(([label, current, previous]) => <div key={label}><b>{label}</b><span>{current}{label === 'Gate confidence' ? '%' : ''}</span><span>{previous ?? '—'}{previous !== undefined && label === 'Gate confidence' ? '%' : ''}</span><em className={previous === undefined || current === previous ? '' : current > previous ? 'up' : 'down'}>{previous === undefined ? '—' : current === previous ? 'no change' : `${current > previous ? '+' : ''}${current - previous}`}</em></div>)}</div>}
      {!loading && !baseline && <p className="empty panel-empty">Choose another run of this target to compare coverage, defects, and gate confidence.</p>}
    </section>
  )
}

async function fetchDetails(runId: string): Promise<{ details: RunDetails; errors: string[] }> {
  const results = await Promise.allSettled([
    api.tasks(runId), api.events(runId), api.coverage(runId), api.defects(runId), api.gate(runId),
  ])
  const [tasks, events, coverage, defects, gate] = results
  const errors = results.flatMap((result, index) => {
    if (result.status === 'fulfilled') return []
    if (index === 4 && result.reason instanceof ApiError && result.reason.status === 404) return []
    return [result.reason instanceof Error ? result.reason.message : String(result.reason)]
  })
  return {
    details: {
      tasks: tasks.status === 'fulfilled' ? tasks.value.items : [],
      events: events.status === 'fulfilled' ? events.value.items : [],
      coverage: coverage.status === 'fulfilled' ? coverage.value : undefined,
      defects: defects.status === 'fulfilled' ? defects.value : [],
      gate: gate.status === 'fulfilled' ? gate.value : undefined,
    },
    errors,
  }
}

function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<string>()
  const [details, setDetails] = useState<RunDetails>(EMPTY_DETAILS)
  const [loadingRuns, setLoadingRuns] = useState(true)
  const [loadingDetails, setLoadingDetails] = useState(false)
  const [action, setAction] = useState<'create' | 'cancel' | 'rerun'>()
  const [error, setError] = useState<string>()

  function mergeRun(run: Run) {
    setRuns((items) => [run, ...items.filter((item) => item.id !== run.id)])
  }

  useEffect(() => {
    let current = true
    async function refresh() {
      try {
        const items = await api.runs()
        if (!current) return
        const sorted = [...items].sort((left, right) => (right.started_at ?? '').localeCompare(left.started_at ?? ''))
        setRuns(sorted)
        setSelected((value) => value && sorted.some((item) => item.id === value) ? value : sorted[0]?.id)
        setError(undefined)
      } catch (reason) {
        if (current) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (current) setLoadingRuns(false)
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 8_000)
    return () => { current = false; window.clearInterval(timer) }
  }, [])

  useEffect(() => {
    if (!selected) return
    const runId = selected
    let current = true
    async function refresh(silent = false) {
      if (!silent) setLoadingDetails(true)
      const result = await fetchDetails(runId)
      if (!current) return
      setDetails(result.details)
      if (result.errors.length) setError(`Some run data could not be loaded: ${result.errors.join('; ')}`)
      else setError(undefined)
      setLoadingDetails(false)
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(true), 3_000)
    return () => { current = false; window.clearInterval(timer) }
  }, [selected])

  const run = useMemo(() => runs.find((item) => item.id === selected), [runs, selected])
  const policyViolations = Number(details.gate?.metrics.policy_violations_executed ?? 0)

  async function createRun(request: CreateRunRequest) {
    setAction('create'); setError(undefined)
    try {
      const created = await api.createRun(request)
      mergeRun(created); setDetails(EMPTY_DETAILS); setSelected(created.id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason)); throw reason
    } finally { setAction(undefined) }
  }

  async function cancelRun() {
    if (!run || TERMINAL.has(run.status)) return
    setAction('cancel'); setError(undefined)
    try { mergeRun(await api.cancelRun(run.id)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setAction(undefined) }
  }

  async function rerun() {
    if (!run) return
    setAction('rerun'); setError(undefined)
    try {
      const created = await api.createRun({ ...run.request, trigger: 'manual', commit_sha: run.request.commit_sha ?? undefined })
      mergeRun(created); setDetails(EMPTY_DETAILS); setSelected(created.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setAction(undefined) }
  }

  return (
    <div className="shell">
      <RunList runs={runs} selected={selected} onSelect={(id) => { setDetails(EMPTY_DETAILS); setSelected(id) }} onCreate={createRun} creating={action === 'create'} />
      <main>
        <header className="top">
          <div><small>SUPERVISED AUTONOMOUS QUALITY ASSURANCE</small><h1>{run ? `${run.request.target_id} / ${short(run.id)}` : loadingRuns ? 'Loading control room…' : 'No run selected'}</h1>{run && <p>Seed {run.request.seed} · {run.request.personas.join(', ')} · {run.request.policy_profile}</p>}</div>
          <div className="run-actions">
            {run && <Status value={run.status} />}
            <button className="secondary" onClick={() => void rerun()} disabled={!run || !!action}>{action === 'rerun' ? 'Starting…' : 'Rerun'}</button>
            <button className="danger" onClick={() => void cancelRun()} disabled={!run || TERMINAL.has(run.status) || !!action}>{action === 'cancel' ? 'Cancelling…' : 'Cancel'}</button>
          </div>
        </header>
        {error && <div className="error" role="alert"><span>{error}</span><button onClick={() => setError(undefined)} aria-label="Dismiss error">×</button></div>}
        {loadingDetails && <div className="loading-bar"><i /></div>}
        <div className="metrics">
          <article><small>LOGICAL AGENTS</small><b>{new Set(details.tasks.map((item) => item.agent_type)).size}</b><em>static registry</em></article>
          <article><small>EVIDENCE EVENTS</small><b>{details.events.length}</b><em>append-only audit</em></article>
          <article><small>POLICY VIOLATIONS</small><b>{policyViolations}</b><em>executed actions</em></article>
          <article><small>RELEASE GATE</small><b>{details.gate?.outcome.toUpperCase() ?? '—'}</b><em>{details.gate ? `${Math.round(details.gate.release_confidence * 100)}% confidence` : 'pending evidence'}</em></article>
        </div>
        <div className="grid">
          <TaskTable tasks={details.tasks} />
          <CoverageGraph coverage={details.coverage} />
          <Timeline events={details.events} />
          <DefectList defects={details.defects} />
          <GatePanel gate={details.gate} />
          {run && (
            <ReportCentre
              key={run.id}
              runId={run.id}
              gateReady={Boolean(details.gate)}
            />
          )}
          {run && <Comparison run={run} runs={runs} details={details} />}
        </div>
      </main>
    </div>
  )
}

export default App
