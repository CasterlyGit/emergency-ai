// k6 load test for emergency-ai
// Run: k6 run --vus 50 --duration 30s scripts/load_test.js
//
// Stages: ramp 0→10 VUs over 10s, hold 50 VUs for 30s, ramp down to 0 over 10s.
// Thresholds: p95 < 2500ms, error rate < 1%.

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

const CITIES = ["new-york", "san-francisco", "london", "tokyo", "mumbai", "bangalore"];

const SITUATIONS = [
  "Person collapsed, not breathing",
  "Kitchen fire spreading to curtains",
  "Child choking on food",
  "Severe allergic reaction, face swelling",
  "Car accident with injured passenger",
  "Person unresponsive after fall",
  "Gas smell in apartment building",
  "Drowning victim pulled from water",
  "Chest pain and arm numbness",
  "Seizure lasting more than 5 minutes",
];

export const options = {
  stages: [
    { duration: "10s", target: 10 },   // ramp up
    { duration: "30s", target: 50 },   // hold
    { duration: "10s", target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ["p(95)<2500"],  // p95 under 2.5s
    http_req_failed: ["rate<0.01"],     // error rate under 1%
  },
  summaryTrendStats: ["p(50)", "p(95)", "p(99)", "min", "max"],
};

export default function () {
  const city = CITIES[Math.floor(Math.random() * CITIES.length)];
  const situation = SITUATIONS[Math.floor(Math.random() * SITUATIONS.length)];

  const payload = JSON.stringify({ situation, city });

  const params = {
    headers: {
      "Content-Type": "application/json",
    },
    timeout: "10s",
  };

  const res = http.post(`${BASE_URL}/emergency`, payload, params);

  check(res, {
    "status 200": (r) => r.status === 200,
    "has urgency field": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.urgency !== undefined;
      } catch {
        return false;
      }
    },
    "has meta": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body._meta !== undefined;
      } catch {
        return false;
      }
    },
  });

  sleep(0.1);
}
