import type {
  AgentTask,
  Coverage,
  CreateDeliveryRequest,
  CreateRunRequest,
  Defect,
  Delivery,
  DeliveryDestination,
  EventItem,
  Gate,
  Run,
  RunReport,
} from './types'

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    const body = await response.text()
    throw new ApiError(response.status, body || response.statusText || 'Request failed')
  }
  return response.json() as Promise<T>
}

export const api = {
  runs: () => request<Run[]>('/v1/runs'),
  createRun: (run: CreateRunRequest) =>
    request<Run>('/v1/runs', { method: 'POST', body: JSON.stringify(run) }),
  cancelRun: (runId: string) => request<Run>(`/v1/runs/${runId}/cancel`, { method: 'POST' }),
  tasks: (runId: string) => request<{ items: AgentTask[] }>(`/v1/runs/${runId}/tasks`),
  events: (runId: string) =>
    request<{ items: EventItem[] }>(`/v1/runs/${runId}/events?limit=500`),
  coverage: (runId: string) => request<Coverage>(`/v1/runs/${runId}/coverage`),
  defects: (runId: string) => request<Defect[]>(`/v1/runs/${runId}/defects`),
  gate: (runId: string) => request<Gate>(`/v1/gates/${runId}`),
  report: (runId: string) => request<RunReport>(`/v1/runs/${runId}/report`),
  regenerateReport: (runId: string) =>
    request<RunReport>(`/v1/runs/${runId}/report/regenerate`, { method: 'POST' }),
  deliveryDestinations: () =>
    request<{ items: DeliveryDestination[] }>('/v1/delivery-destinations'),
  deliveries: (runId: string) =>
    request<{ items: Delivery[] }>(`/v1/runs/${runId}/deliveries`),
  createDelivery: (runId: string, delivery: CreateDeliveryRequest) =>
    request<Delivery>(`/v1/runs/${runId}/deliveries`, {
      method: 'POST',
      body: JSON.stringify(delivery),
    }),
  approveDelivery: (deliveryId: string) =>
    request<Delivery>(`/v1/deliveries/${deliveryId}/approve`, { method: 'POST' }),
  retryDelivery: (deliveryId: string) =>
    request<Delivery>(`/v1/deliveries/${deliveryId}/retry`, { method: 'POST' }),
}

export function artifactHref(reference: string): string | undefined {
  const id = reference.startsWith('artifact://') ? reference.slice('artifact://'.length) : reference
  return /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(id) ? `/v1/artifacts/${id}` : undefined
}
