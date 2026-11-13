// k6 load test run inside the compose network (no host port-forwarding overhead):
//   docker run --rm -i --network fuel-route-planner_default -e BASE=http://nginx \
//     -e VUS=64 -e DURATION=30s grafana/k6 run - < deploy/loadtest/k6-mixed.js
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE || "http://nginx";
const PAIRS = [
  ["Chicago, IL", "Los Angeles, CA"], ["New York, NY", "Miami, FL"], ["Seattle, WA", "Dallas, TX"],
  ["Denver, CO", "Atlanta, GA"], ["Boston, MA", "Houston, TX"], ["Phoenix, AZ", "Orlando, FL"],
  ["San Francisco, CA", "Chicago, IL"], ["Dallas, TX", "Denver, CO"],
];
const PREFIXES = ["chi", "los", "new y", "sea", "dal", "den", "atl", "bos", "hou", "pho", "san", "mia"];

export const options = {
  vus: parseInt(__ENV.VUS || "64"),
  duration: __ENV.DURATION || "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:route}": ["p(95)<1000"],
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

const pick = (a) => a[Math.floor(Math.random() * a.length)];
const enc = encodeURIComponent;
// Send the public host name: Django rejects unknown Host headers (ALLOWED_HOSTS).
const headers = { headers: { "Accept-Encoding": "gzip", Host: __ENV.HOST || "localhost" } };

export default function () {
  const r = Math.random();
  let res;
  if (r < 0.7) {
    const [s, f] = pick(PAIRS);
    res = http.get(`${BASE}/api/route/?start=${enc(s)}&finish=${enc(f)}`, { ...headers, tags: { kind: "route" } });
  } else if (r < 0.9) {
    res = http.get(`${BASE}/api/places/?q=${enc(pick(PREFIXES))}`, { ...headers, tags: { kind: "places" } });
  } else {
    res = http.get(`${BASE}/api/health/`, { ...headers, tags: { kind: "health" } });
  }
  check(res, { "status 200": (x) => x.status === 200 });
}
