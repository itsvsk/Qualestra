import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from './App'
import type { Defect, Gate, Run } from './types'

const currentId = '11111111-1111-4111-8111-111111111111'
const baselineId = '22222222-2222-4222-8222-222222222222'
const createdId = '33333333-3333-4333-8333-333333333333'
const artifactId = '44444444-4444-4444-8444-444444444444'

function run(id: string, status: Run['status'], seed: number): Run {
  return {
    id,
    status,
    cancellation_requested: status === 'cancelled',
    started_at: id === currentId ? '2026-07-18T10:00:00Z' : '2026-07-17T10:00:00Z',
    ended_at: status === 'exploring' ? null : '2026-07-18T10:05:00Z',
    request: {
      target_id: 'atlas-local', trigger: 'manual', seed, commit_sha: 'abc123',
      changed_files: [], personas: ['owner', 'viewer'], browsers: ['chromium'],
      budget: { max_minutes: 12, max_actions: 80, max_llm_cost_usd: 2, max_api_requests: 500 },
      policy_profile: 'ephemeral_ci',
    },
  }
}

const currentRun = run(currentId, 'exploring', 41021)
const baselineRun = run(baselineId, 'passed', 41020)
const createdRun = run(createdId, 'exploring', 50000)

const defect: Defect = {
  defect_id: '55555555-5555-4555-8555-555555555555', run_id: currentId,
  title: 'Tenant isolation failed on /projects', severity: 'critical', confidence: 1,
  status: 'verified', affected_area: '/projects', signature: 'tenant|isolation',
  persona: 'viewer', preconditions: ['seeded'], steps_to_reproduce: [{ action: 'open_project' }],
  expected: 'Only owned projects are visible', actual: 'Another tenant is visible',
  verification: { attempts: 3, matched: 3 },
  evidence: [{ kind: 'evidence', artifact_id: `artifact://${artifactId}` }],
  suspected_component: 'project query', suggested_investigation: 'Inspect the tenant filter.',
  created_at: '2026-07-18T10:04:00Z',
}

const gate: Gate = {
  outcome: 'block', release_confidence: 0.96, blockers: ['verified_critical_defects'],
  generated_at: '2026-07-18T10:05:00Z',
  metrics: { policy_violations_executed: 0, verified_critical_defects: 1 },
  rules: [{ metric: 'verified_critical_defects', operator: '>', threshold: 0, actual: 1, matched: true, level: 'block' }],
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/v1/runs' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { seed: number }
      return json({ ...createdRun, request: { ...createdRun.request, seed: body.seed } }, 202)
    }
    if (path === `/v1/runs/${currentId}/cancel` && init?.method === 'POST') return json({ ...currentRun, status: 'cancelled', cancellation_requested: true })
    if (path === '/v1/runs') return json([currentRun, baselineRun])
    if (path.endsWith('/tasks')) return json({ items: [{ task_id: '66666666-6666-4666-8666-666666666666', agent_type: 'ui_explorer', status: 'running', target_partition: 'owner:/projects', dependency_task_ids: [], budget: { max_seconds: 300, max_tool_calls: 40, max_model_tokens: 0, max_api_requests: 10, max_artifact_bytes: 1000 }, attempt: 1 }] })
    if (path.includes('/events')) return json({ items: [{ event_id: '77777777-7777-4777-8777-777777777777', event_type: 'state.observed', producer: 'control-api', occurred_at: '2026-07-18T10:02:00Z', payload: { route: '/projects' } }], next_offset: 1 })
    if (path === `/v1/runs/${baselineId}/coverage`) return json({ states: 1, transitions: 0, routes: 1, roles: 1, graph: { nodes: [], edges: [] } })
    if (path.endsWith('/coverage')) return json({ states: 2, transitions: 1, routes: 1, roles: 2, graph: { nodes: [{ id: 'owner-projects-state', route: '/projects', role: 'owner', visited_count: 2 }, { id: 'viewer-projects-state', route: '/projects', role: 'viewer', visited_count: 1 }], edges: [{ from_state_id: 'owner-projects-state', to_state_id: 'viewer-projects-state', outcome: 'succeeded' }] } })
    if (path === `/v1/runs/${baselineId}/defects`) return json([])
    if (path.endsWith('/defects')) return json([defect])
    if (path === `/v1/gates/${baselineId}`) return json({ ...gate, outcome: 'pass', release_confidence: 1, blockers: [], rules: [] })
    if (path === `/v1/gates/${createdId}`) return json({ detail: 'gate not available' }, 404)
    if (path.startsWith('/v1/gates/')) return json(gate)
    return json({ detail: `Unhandled ${path}` }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('operator control room', () => {
  test('shows live run evidence, coverage, gate operands, and comparison', async () => {
    render(<App />)

    expect(await screen.findByText('atlas-local / 11111111')).toBeInTheDocument()
    expect(screen.getByText('Observed state graph')).toBeInTheDocument()
    expect(await screen.findByText('Tenant isolation failed on /projects')).toBeInTheDocument()
    expect(screen.getByText('Actual')).toBeInTheDocument()
    expect(screen.getAllByText('96% confidence')).not.toHaveLength(0)

    fireEvent.click(screen.getByText('Tenant isolation failed on /projects'))
    const evidence = await screen.findByRole('link', { name: /evidence 1/i })
    expect(evidence).toHaveAttribute('href', `/v1/artifacts/${artifactId}`)
    expect(evidence).toHaveAttribute('download')

    fireEvent.change(screen.getByLabelText('Comparison baseline'), { target: { value: baselineId } })
    expect((await screen.findAllByText('+1')).length).toBeGreaterThan(0)
    expect(screen.getByText('Verified defects')).toBeInTheDocument()
  })

  test('creates, cancels, and reproducibly reruns without mutating history', async () => {
    render(<App />)
    await screen.findByText('atlas-local / 11111111')

    fireEvent.click(screen.getByRole('button', { name: '+ New run' }))
    fireEvent.change(screen.getByLabelText('Deterministic seed'), { target: { value: '50000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
    expect(await screen.findByText('atlas-local / 33333333')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/v1/runs', expect.objectContaining({ method: 'POST', body: expect.stringContaining('50000') }))

    fireEvent.click(screen.getByRole('button', { name: 'Rerun' }))
    await waitFor(() => expect(fetchMock.mock.calls.filter(([path, init]) => path === '/v1/runs' && init?.method === 'POST')).toHaveLength(2))

    const currentRunLabel = screen.getAllByText(/#11111111/).find((element) => element.tagName === 'SMALL')
    fireEvent.click(currentRunLabel?.closest('button') as HTMLButtonElement)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(`/v1/runs/${currentId}/cancel`, expect.objectContaining({ method: 'POST' })))
  })

  test('keeps the supervised safety boundary visible with no runs', async () => {
    fetchMock.mockImplementationOnce(async () => json([]))
    render(<App />)
    expect(screen.getByText('Qualestra')).toBeInTheDocument()
    expect(screen.getByText(/AI proposes/)).toBeInTheDocument()
    expect(await screen.findByText('No run selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+ New run' })).toBeEnabled()
  })

  test('polls selected run details for live progress', async () => {
    const intervals = vi.spyOn(window, 'setInterval')
    render(<App />)
    await screen.findByText('atlas-local / 11111111')
    const detailsPoll = intervals.mock.calls.find(([, delay]) => delay === 3_000)
    expect(detailsPoll).toBeDefined()
    const previousTaskReads = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/tasks')).length

    await act(async () => { (detailsPoll?.[0] as () => void)() })
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/tasks')).length).toBeGreaterThan(previousTaskReads)
    })
  })
})
