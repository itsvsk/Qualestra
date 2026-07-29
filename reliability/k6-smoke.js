import http from 'k6/http'
import { check } from 'k6'

export const options = { vus: 5, duration: '20s', thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<250'] } }
export default function () {
  const response = http.get(`${__ENV.ATLAS_API_URL || 'http://localhost:8080'}/v1/projects`, { headers: { 'x-atlas-persona': 'editor' } })
  check(response, { 'status is 200': value => value.status === 200 })
}
