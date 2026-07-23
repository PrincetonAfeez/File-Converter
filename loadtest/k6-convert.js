// k6 load test for the File Converter core path: login -> upload CSV -> poll -> download.
// Run against a STAGING instance (never production). Requires k6 (https://k6.io).
//
//   k6 run -e BASE_URL=https://staging.example.com -e USER=demo -e PASS=demo12345 \
//          -e VUS=20 -e DURATION=2m loadtest/k6-convert.js
//
// Records against the SLOs in docs/SLO.md (p95 latency, error rate).

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const USER = __ENV.USER || "demo";
const PASS = __ENV.PASS || "demo12345";

const uploadLatency = new Trend("upload_latency", true);
const statusLatency = new Trend("status_latency", true);
const errors = new Rate("errors");

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || "1m",
  thresholds: {
    // Aligns with docs/SLO.md: upload accept p95 < 1s, status poll p95 < 200ms, <0.5% errors.
    upload_latency: ["p(95)<1000"],
    status_latency: ["p(95)<200"],
    errors: ["rate<0.005"],
  },
};

function csrf(body) {
  const m = body.match(/name="csrfmiddlewaretoken" value="([^"]+)"/);
  return m ? m[1] : "";
}

export default function () {
  // 1. Login
  const loginPage = http.get(`${BASE}/accounts/login/`);
  const token = csrf(loginPage.body);
  const login = http.post(`${BASE}/accounts/login/`, {
    csrfmiddlewaretoken: token,
    username: USER,
    password: PASS,
  });
  check(login, { "logged in": (r) => r.status === 200 || r.status === 302 });

  // 2. Upload a small CSV
  const dash = http.get(`${BASE}/`);
  const upToken = csrf(dash.body);
  const idem = `${__VU}-${__ITER}-${Date.now()}`;
  const res = http.post(`${BASE}/`, {
    csrfmiddlewaretoken: upToken,
    target_format: "json",
    idempotency_key: idem,
    file: http.file("name,value\nAda,1\nGrace,2\n", "data.csv", "text/csv"),
  });
  uploadLatency.add(res.timings.duration);
  const ok = check(res, { "upload accepted": (r) => r.status === 200 || r.status === 302 });
  errors.add(!ok);

  // 3. Poll job status a few times (best-effort; job URL parsed from redirect)
  const jobUrl = res.url && res.url.includes("/jobs/") ? res.url : null;
  if (jobUrl) {
    for (let i = 0; i < 3; i++) {
      const s = http.get(`${jobUrl}status/`);
      statusLatency.add(s.timings.duration);
      sleep(1);
    }
  }
  sleep(1);
}
