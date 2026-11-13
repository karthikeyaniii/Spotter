// Hammer one URL to find its throughput ceiling (used to isolate bottlenecks per endpoint):
//   docker run --rm -i --network fuel-route-planner_default -e URL=http://nginx/api/health/live/ \
//     grafana/k6 run - < deploy/loadtest/k6-single.js
import http from "k6/http";
export const options = { vus: 64, duration: "15s" };
export default function () { http.get(__ENV.URL, { headers: { Host: "localhost" } }); }
