import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { ReportCentre } from './ReportCentre'
import type { Delivery, DeliveryDestination, RunReport } from './types'

const runId = '11111111-1111-4111-8111-111111111111'
const reportId = '22222222-2222-4222-8222-222222222222'
const pendingId = '33333333-3333-4333-8333-333333333333'
const failedId = '44444444-4444-4444-8444-444444444444'

const report: RunReport = {
  run_id: runId,
  schema_version: '1.0',
  generated_at: '2026-07-27T10:00:00Z',
  title: 'Atlas deterministic quality report',
  summary: 'One independently verified defect blocks this release.',
  gate_outcome: 'block',
  verified_defect_count: 1,
  documents: [
    { report_id: reportId, format: 'markdown', media_type: 'text/markdown', content: '# Atlas report\n\nGate: BLOCK', digest: 'sha256:markdown' },
    { report_id: '77777777-7777-4777-8777-777777777777', format: 'html', media_type: 'text/html', content: '<h1>Atlas report</h1>', digest: 'sha256:html' },
    { report_id: '88888888-8888-4888-8888-888888888888', format: 'json', media_type: 'application/json', content: '{"gate":"block"}', digest: 'sha256:json' },
  ],
}

const destinations: DeliveryDestination[] = [
  {
    connector_id: 'github-issues',
    destination_id: 'release-defects',
    display_name: 'Release defects',
    kind: 'ticket',
    provider: 'GitHub',
    supported_modes: ['dry_run', 'manual', 'automatic'],
  },
  {
    connector_id: 'slack',
    destination_id: 'release-alerts',
    display_name: 'Release alerts',
    kind: 'alert',
    provider: 'Slack',
    supported_modes: ['dry_run', 'manual', 'automatic'],
  },
  {
    connector_id: 'filesystem',
    destination_id: 'local-report',
    display_name: 'Local report archive',
    kind: 'artifact',
    provider: 'filesystem',
    supported_modes: ['dry_run', 'manual', 'automatic'],
  },
]

function delivery(
  deliveryId: string,
  status: Delivery['status'],
  mode: Delivery['mode'] = 'manual',
): Delivery {
  return {
    delivery_id: deliveryId,
    run_id: runId,
    report_id: reportId,
    connector_id: 'filesystem',
    destination_id: 'local-report',
    mode,
    status,
    created_at: '2026-07-27T10:01:00Z',
    updated_at: '2026-07-27T10:02:00Z',
    attempts: status === 'failed'
      ? [{ attempt: 1, status: 'failed', recorded_at: '2026-07-27T10:02:00Z', error: 'Temporary delivery failure' }]
      : [],
  }
}

function externalTicketDelivery(): Delivery {
  return {
    ...delivery('99999999-9999-4999-8999-999999999999', 'succeeded'),
    connector_id: 'github_issues',
    destination_id: 'release-defects',
    external_reference: 'I_SAFE',
    external_url: 'https://github.com/example/project/issues/42',
  }
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === `/v1/runs/${runId}/report/regenerate` && init?.method === 'POST') {
      return json({ ...report, generated_at: '2026-07-27T11:00:00Z' })
    }
    if (path === '/v1/delivery-destinations') return json({ items: destinations })
    if (path === `/v1/runs/${runId}/report`) return json(report)
    if (path === `/v1/runs/${runId}/deliveries` && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { mode: Delivery['mode'] }
      return json(delivery(
        '55555555-5555-4555-8555-555555555555',
        body.mode === 'manual' ? 'pending_approval' : 'succeeded',
        body.mode,
      ))
    }
    if (path === `/v1/runs/${runId}/deliveries`) {
      return json({ items: [delivery(pendingId, 'pending_approval'), delivery(failedId, 'failed')] })
    }
    if (path === `/v1/deliveries/${pendingId}/approve` && init?.method === 'POST') {
      return json(delivery(pendingId, 'queued'))
    }
    if (path === `/v1/deliveries/${failedId}/retry` && init?.method === 'POST') {
      return json(delivery(failedId, 'queued'))
    }
    return json({ detail: `Unhandled ${path}` }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const TestURL = class extends URL {}
  Object.assign(TestURL, {
    createObjectURL: vi.fn(() => 'blob:report'),
    revokeObjectURL: vi.fn(),
  })
  vi.stubGlobal('URL', TestURL)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('report centre', () => {
  test('previews each deterministic format and downloads the selected document', async () => {
    render(<ReportCentre runId={runId} />)

    expect(await screen.findByText('Atlas deterministic quality report')).toBeInTheDocument()
    expect(screen.getByText((_, element) => element?.textContent === '# Atlas report\n\nGate: BLOCK')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'html' }))
    expect(screen.getByText('<h1>Atlas report</h1>')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })

  test('creates controlled deliveries and supports approve and retry actions', async () => {
    render(<ReportCentre runId={runId} />)
    await screen.findByText('GitHub · release-defects')

    fireEvent.change(screen.getByLabelText('Delivery mode'), { target: { value: 'manual' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create approval request' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/v1/runs/${runId}/deliveries`,
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"connector_id":"github-issues"'),
      }),
    ))
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/runs/${runId}/deliveries`,
      expect.objectContaining({
        body: expect.stringContaining('"destination_id":"release-defects"'),
      }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      `/v1/runs/${runId}/deliveries`,
      expect.objectContaining({
        body: expect.stringContaining('"mode":"manual"'),
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Approve delivery 33333333' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/v1/deliveries/${pendingId}/approve`,
      expect.objectContaining({ method: 'POST' }),
    ))

    fireEvent.click(screen.getByRole('button', { name: 'Retry delivery 44444444' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/v1/deliveries/${failedId}/retry`,
      expect.objectContaining({ method: 'POST' }),
    ))
  })

  test('shows ticket and alert intent and supports configured automatic delivery', async () => {
    render(<ReportCentre runId={runId} />)
    await screen.findByText('GitHub · release-defects')

    fireEvent.click(screen.getByRole('button', { name: 'Alert' }))
    expect(await screen.findByText('Slack · release-alerts')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Delivery mode'), { target: { value: 'automatic' } })
    expect(screen.getByText(/Automatic mode sends immediately/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Deliver automatically' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/v1/runs/${runId}/deliveries`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          report_id: reportId,
          connector_id: 'slack',
          destination_id: 'release-alerts',
          mode: 'automatic',
        }),
      }),
    ))
  })

  test('renders a validated provider ticket link from a completed delivery', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/delivery-destinations') return json({ items: destinations })
      if (path === `/v1/runs/${runId}/report`) return json(report)
      if (path === `/v1/runs/${runId}/deliveries`) {
        return json({ items: [externalTicketDelivery()] })
      }
      return json({}, 404)
    })

    const { container } = render(<ReportCentre runId={runId} />)

    await screen.findByText('I_SAFE')
    fireEvent.click(container.querySelector('.delivery-list summary') as HTMLElement)
    const link = await screen.findByRole('link', { name: 'Open external ticket' })
    expect(link).toHaveAttribute('href', 'https://github.com/example/project/issues/42')
  })

  test('keeps reports usable when no delivery provider is configured', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/delivery-destinations') return json({ items: [] })
      if (path === `/v1/runs/${runId}/report`) return json(report)
      if (path === `/v1/runs/${runId}/deliveries`) return json({ items: [] })
      return json({}, 404)
    })

    render(<ReportCentre runId={runId} />)
    expect(await screen.findByText('Atlas deterministic quality report')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('No delivery providers are configured')
    expect(screen.getByRole('button', { name: 'Create dry run' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Ticket' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Alert' })).toBeDisabled()
  })

  test('offers deterministic generation when no report exists and surfaces sanitized errors', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/report/regenerate') && init?.method === 'POST') {
        return json({ detail: 'Report evidence is incomplete' }, 409)
      }
      if (path === '/v1/delivery-destinations') return json({ items: destinations })
      if (path.endsWith('/report')) return json({ detail: 'Not found' }, 404)
      if (path.endsWith('/deliveries')) return json({ items: [] })
      return json({}, 404)
    })

    render(<ReportCentre runId={runId} />)
    expect(await screen.findByText('No deterministic report has been generated for this run.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generate report' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Report evidence is incomplete')
  })

  test('refreshes the report automatically when an in-progress run reaches its release gate', async () => {
    let reportReady = false
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/delivery-destinations') return json({ items: destinations })
      if (path.endsWith('/deliveries')) return json({ items: [] })
      if (path.endsWith('/report')) {
        return reportReady
          ? json(report)
          : json({ detail: 'reports are available only after the release gate is decided' }, 409)
      }
      return json({}, 404)
    })

    const { rerender } = render(<ReportCentre runId={runId} gateReady={false} />)
    expect(
      await screen.findByText(
        'The report will be generated automatically after the release gate is decided.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate report' })).toBeDisabled()

    reportReady = true
    rerender(<ReportCentre runId={runId} gateReady />)

    expect(await screen.findByText('Atlas deterministic quality report')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
