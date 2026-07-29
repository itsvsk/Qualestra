import { FormEvent, useEffect, useMemo, useState } from 'react'
import { ApiError, api } from './api'
import type {
  CreateDeliveryRequest,
  Delivery,
  DeliveryDestination,
  DeliveryKind,
  DeliveryMode,
  ReportDocument,
  ReportFormat,
  RunReport,
} from './types'

const FORMATS: ReportFormat[] = ['markdown', 'html', 'json']
const DELIVERY_KINDS: DeliveryKind[] = ['ticket', 'alert', 'artifact']
const DELIVERY_MODES: { mode: DeliveryMode; label: string }[] = [
  { mode: 'dry_run', label: 'Dry run' },
  { mode: 'manual', label: 'Manual approval' },
  { mode: 'automatic', label: 'Automatic delivery' },
]

function destinationKey(destination: DeliveryDestination) {
  return `${destination.connector_id}:${destination.destination_id}`
}

function displayKind(kind: DeliveryKind) {
  return kind === 'artifact' ? 'Archive' : `${kind[0].toUpperCase()}${kind.slice(1)}`
}

function safeExternalReference(reference?: string | null) {
  if (!reference || reference.includes('://') || /^(?:env|secret|credential):/i.test(reference)) {
    return undefined
  }
  return reference
}

function safeExternalUrl(reference?: string | null) {
  if (!reference) return undefined
  try {
    const parsed = new URL(reference)
    if (
      !['http:', 'https:'].includes(parsed.protocol)
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
    ) {
      return undefined
    }
    return parsed.toString()
  } catch {
    return undefined
  }
}

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason)
}

function replaceDelivery(items: Delivery[], delivery: Delivery) {
  return [delivery, ...items.filter((item) => item.delivery_id !== delivery.delivery_id)]
}

async function readReportCentre(runId: string, gateReady: boolean) {
  const [reportResult, deliveryResult, destinationResult] = await Promise.allSettled([
    gateReady ? api.report(runId) : Promise.resolve(undefined),
    api.deliveries(runId),
    api.deliveryDestinations(),
  ])
  const errors: string[] = []
  if (
    reportResult.status === 'rejected'
    && !(reportResult.reason instanceof ApiError && reportResult.reason.status === 404)
  ) {
    errors.push(`Report could not be loaded: ${message(reportResult.reason)}`)
  }
  if (deliveryResult.status === 'rejected') {
    errors.push(`Delivery history could not be loaded: ${message(deliveryResult.reason)}`)
  }
  return {
    report: reportResult.status === 'fulfilled' ? reportResult.value : undefined,
    deliveries: deliveryResult.status === 'fulfilled' ? deliveryResult.value.items : [],
    destinations: destinationResult.status === 'fulfilled' ? destinationResult.value.items : [],
    catalogError: destinationResult.status === 'rejected'
      ? `Configured destinations could not be loaded: ${message(destinationResult.reason)}`
      : undefined,
    errors,
  }
}

function downloadDocument(report: RunReport, document: ReportDocument) {
  const extension = document.format === 'markdown' ? 'md' : document.format
  const blob = new Blob([document.content], { type: document.media_type })
  const url = URL.createObjectURL(blob)
  const anchor = window.document.createElement('a')
  anchor.href = url
  anchor.download = `sentinelqa-${report.run_id.slice(0, 8)}.${extension}`
  anchor.click()
  URL.revokeObjectURL(url)
}

function DeliveryHistory({
  deliveries,
  destinations,
  busy,
  onApprove,
  onRetry,
}: {
  deliveries: Delivery[]
  destinations: DeliveryDestination[]
  busy?: string
  onApprove: (delivery: Delivery) => Promise<void>
  onRetry: (delivery: Delivery) => Promise<void>
}) {
  if (!deliveries.length) {
    return <p className="empty">No publication attempts for this run.</p>
  }
  return (
    <div className="delivery-list">
      {deliveries.map((delivery) => (
        <details key={delivery.delivery_id}>
          <summary>
            <span className={`delivery-state delivery-${delivery.status}`}>{delivery.status.replaceAll('_', ' ')}</span>
            <span>
              <b>
                {destinations.find((destination) => (
                  destination.connector_id === delivery.connector_id
                  && destination.destination_id === delivery.destination_id
                ))?.display_name ?? delivery.destination_id}
              </b>
              <small>{delivery.mode.replaceAll('_', ' ')} · {delivery.destination_id}</small>
            </span>
            <span className="delivery-actions">
              {delivery.status === 'pending_approval' && (
                <button
                  className="primary"
                  type="button"
                  aria-label={`Approve delivery ${delivery.delivery_id.slice(0, 8)}`}
                  disabled={busy === delivery.delivery_id}
                  onClick={(event) => {
                    event.preventDefault()
                    void onApprove(delivery)
                  }}
                >
                  {busy === delivery.delivery_id ? 'Approving…' : 'Approve'}
                </button>
              )}
              {delivery.status === 'failed' && (
                <button
                  className="secondary"
                  type="button"
                  aria-label={`Retry delivery ${delivery.delivery_id.slice(0, 8)}`}
                  disabled={busy === delivery.delivery_id}
                  onClick={(event) => {
                    event.preventDefault()
                    void onRetry(delivery)
                  }}
                >
                  {busy === delivery.delivery_id ? 'Retrying…' : 'Retry'}
                </button>
              )}
              <i aria-hidden="true">⌄</i>
            </span>
          </summary>
          <div className="delivery-detail">
            <dl>
              <div><dt>Delivery</dt><dd>#{delivery.delivery_id.slice(0, 8)}</dd></div>
              <div><dt>Updated</dt><dd>{new Date(delivery.updated_at).toLocaleString()}</dd></div>
              {safeExternalReference(delivery.external_reference) && (
                <div>
                  <dt>External reference</dt>
                  <dd>{safeExternalReference(delivery.external_reference)}</dd>
                </div>
              )}
              {safeExternalUrl(delivery.external_url) && (
                <div>
                  <dt>Provider ticket</dt>
                  <dd>
                    <a
                      href={safeExternalUrl(delivery.external_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open external ticket
                    </a>
                  </dd>
                </div>
              )}
            </dl>
            <h3>Attempt history</h3>
            {delivery.attempts.length ? (
              <ol>
                {delivery.attempts.map((attempt) => (
                  <li key={`${delivery.delivery_id}-${attempt.attempt}`}>
                    <b>Attempt {attempt.attempt}: {attempt.status}</b>
                    <span>{new Date(attempt.recorded_at).toLocaleString()}</span>
                    {attempt.error && <p role="alert">{attempt.error}</p>}
                  </li>
                ))}
              </ol>
            ) : <p className="empty">No delivery attempts committed yet.</p>}
          </div>
        </details>
      ))}
    </div>
  )
}

export function ReportCentre({
  runId,
  gateReady = true,
}: {
  runId: string
  gateReady?: boolean
}) {
  const [report, setReport] = useState<RunReport>()
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [destinations, setDestinations] = useState<DeliveryDestination[]>([])
  const [format, setFormat] = useState<ReportFormat>('markdown')
  const [mode, setMode] = useState<DeliveryMode>('dry_run')
  const [kind, setKind] = useState<DeliveryKind>('ticket')
  const [selectedDestination, setSelectedDestination] = useState('')
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<string>()
  const [error, setError] = useState<string>()
  const [catalogError, setCatalogError] = useState<string>()

  async function load() {
    setLoading(true)
    const result = await readReportCentre(runId, gateReady)
    setReport(result.report)
    setDeliveries(result.deliveries)
    setDestinations(result.destinations)
    setCatalogError(result.catalogError)
    setError(result.errors.join(' ') || undefined)
    setLoading(false)
  }

  useEffect(() => {
    let current = true
    async function initialLoad() {
      const result = await readReportCentre(runId, gateReady)
      if (!current) return
      setReport(result.report)
      setDeliveries(result.deliveries)
      setDestinations(result.destinations)
      setCatalogError(result.catalogError)
      setError(result.errors.join(' ') || undefined)
      setLoading(false)
    }
    void initialLoad()
    return () => { current = false }
  }, [gateReady, runId])

  const document = useMemo(
    () => report?.documents.find((item) => item.format === format) ?? report?.documents[0],
    [format, report],
  )
  const effectiveKind = destinations.some((item) => item.kind === kind)
    ? kind
    : DELIVERY_KINDS.find((item) => destinations.some((destination) => (
      destination.kind === item
    ))) ?? kind
  const kindDestinations = useMemo(
    () => destinations.filter((destination) => destination.kind === effectiveKind),
    [destinations, effectiveKind],
  )
  const destination = useMemo(
    () => kindDestinations.find((item) => destinationKey(item) === selectedDestination)
      ?? kindDestinations[0],
    [kindDestinations, selectedDestination],
  )
  const effectiveMode = destination?.supported_modes.includes(mode)
    ? mode
    : destination?.supported_modes[0] ?? mode

  async function regenerate() {
    setAction('regenerate')
    setError(undefined)
    try {
      setReport(await api.regenerateReport(runId))
    } catch (reason) {
      setError(`Report could not be regenerated: ${message(reason)}`)
    } finally {
      setAction(undefined)
    }
  }

  async function createDelivery(event: FormEvent) {
    event.preventDefault()
    if (!document || !destination) return
    const request: CreateDeliveryRequest = {
      report_id: document.report_id,
      connector_id: destination.connector_id,
      destination_id: destination.destination_id,
      mode: effectiveMode,
    }
    setAction('create')
    setError(undefined)
    try {
      const created = await api.createDelivery(runId, request)
      setDeliveries((items) => replaceDelivery(items, created))
    } catch (reason) {
      setError(`Delivery could not be created: ${message(reason)}`)
    } finally {
      setAction(undefined)
    }
  }

  async function updateDelivery(delivery: Delivery, operation: 'approve' | 'retry') {
    setAction(delivery.delivery_id)
    setError(undefined)
    try {
      const updated = operation === 'approve'
        ? await api.approveDelivery(delivery.delivery_id)
        : await api.retryDelivery(delivery.delivery_id)
      setDeliveries((items) => replaceDelivery(items, updated))
    } catch (reason) {
      setError(`Delivery could not be ${operation === 'approve' ? 'approved' : 'retried'}: ${message(reason)}`)
    } finally {
      setAction(undefined)
    }
  }

  return (
    <section className="panel span-2 report-centre">
      <header>
        <div><small>CONTROLLED PUBLICATION</small><h2>Report centre</h2></div>
        <div className="report-actions">
          <button className="secondary" type="button" onClick={() => void load()} disabled={loading || !!action}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
          <button
            className="secondary"
            type="button"
            onClick={() => void regenerate()}
            disabled={!!action || !gateReady}
          >
            {action === 'regenerate' ? 'Regenerating…' : 'Regenerate'}
          </button>
        </div>
      </header>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {loading ? <p className="empty panel-empty">Loading report and delivery history…</p> : (
        <div className="report-layout">
          <div className="report-preview">
            {report ? (
              <>
                <div className="report-meta">
                  <div><small>REPORT</small><b>{report.title}</b><span>#{document?.report_id.slice(0, 8) ?? '—'} · schema {report.schema_version}</span></div>
                  <div><small>GATE</small><b>{report.gate_outcome}</b><span>{report.verified_defect_count} verified defects</span></div>
                </div>
                <p className="report-summary">{report.summary}</p>
                <div className="format-toolbar" role="group" aria-label="Report format">
                  {FORMATS.map((item) => (
                    <button type="button" className={document?.format === item ? 'active' : ''} key={item} onClick={() => setFormat(item)}>
                      {item}
                    </button>
                  ))}
                  <button
                    type="button"
                    className="download-report"
                    disabled={!document}
                    onClick={() => document && downloadDocument(report, document)}
                  >
                    Download
                  </button>
                </div>
                <pre className="report-document">{document?.content ?? 'This format is not available.'}</pre>
              </>
            ) : (
              <div className="report-unavailable">
                <p className="empty">
                  {gateReady
                    ? 'No deterministic report has been generated for this run.'
                    : 'The report will be generated automatically after the release gate is decided.'}
                </p>
                <button
                  className="primary"
                  type="button"
                  onClick={() => void regenerate()}
                  disabled={!!action || !gateReady}
                >
                  {action === 'regenerate' ? 'Generating…' : 'Generate report'}
                </button>
              </div>
            )}
          </div>
          <div className="publication">
            <h3>Create delivery</h3>
            <p>Choose only from destinations configured by an operator. Provider URLs and credentials never enter this control surface.</p>
            <form onSubmit={(event) => void createDelivery(event)}>
              <fieldset className="intent-picker">
                <legend>Delivery intent</legend>
                <div role="group" aria-label="Delivery intent">
                  {DELIVERY_KINDS.map((item) => {
                    const available = destinations.some((destination) => destination.kind === item)
                    return (
                      <button
                        type="button"
                        className={effectiveKind === item ? 'active' : ''}
                        disabled={!available}
                        key={item}
                        onClick={() => setKind(item)}
                      >
                        {displayKind(item)}
                      </button>
                    )
                  })}
                </div>
              </fieldset>
              <label>
                Configured destination
                <select
                  required
                  value={destination ? destinationKey(destination) : ''}
                  disabled={!kindDestinations.length}
                  onChange={(event) => setSelectedDestination(event.target.value)}
                >
                  {!kindDestinations.length && <option value="">No {effectiveKind} destinations configured</option>}
                  {kindDestinations.map((item) => (
                    <option key={destinationKey(item)} value={destinationKey(item)}>
                      {item.display_name} · {item.provider}
                    </option>
                  ))}
                </select>
              </label>
              {destination && (
                <div className={`destination-summary destination-${destination.kind}`}>
                  <span>{displayKind(destination.kind)} destination</span>
                  <b>{destination.display_name}</b>
                  <small>{destination.provider} · {destination.destination_id}</small>
                </div>
              )}
              <label>
                Delivery mode
                <select value={effectiveMode} onChange={(event) => setMode(event.target.value as DeliveryMode)}>
                  {DELIVERY_MODES.map((item) => (
                    <option
                      value={item.mode}
                      key={item.mode}
                      disabled={!destination?.supported_modes.includes(item.mode)}
                    >
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              {effectiveMode === 'automatic' && destination && (
                <p className="mode-note">Automatic mode sends immediately through the configured {destination.provider} provider.</p>
              )}
              <button className="primary wide" disabled={!document || !!action || !destination}>
                {action === 'create'
                  ? 'Creating…'
                  : effectiveMode === 'dry_run'
                    ? 'Create dry run'
                    : effectiveMode === 'manual'
                      ? 'Create approval request'
                      : 'Deliver automatically'}
              </button>
            </form>
            {catalogError && <div className="destination-unavailable" role="status">{catalogError} Report preview and downloads remain available.</div>}
            {!catalogError && !destinations.length && (
              <div className="destination-unavailable" role="status">
                No delivery providers are configured. Report preview and downloads remain available.
              </div>
            )}
            {!catalogError && destinations.length > 0 && destinations.every((item) => item.kind === 'artifact') && (
              <div className="offline-destinations" role="status">
                Offline delivery only. Configure a ticket or alert provider to publish externally.
              </div>
            )}
            <h3>Delivery history</h3>
            <DeliveryHistory
              deliveries={deliveries}
              destinations={destinations}
              busy={action}
              onApprove={(delivery) => updateDelivery(delivery, 'approve')}
              onRetry={(delivery) => updateDelivery(delivery, 'retry')}
            />
          </div>
        </div>
      )}
    </section>
  )
}
