"""Gunicorn settings for production (used by the Docker image)."""
import multiprocessing
import os

bind = "0.0.0.0:8000"
# The request path is CPU-bound Python/NumPy, so parallelism comes from processes:
# * threads only add GIL contention (measured in one process: 1 thread = 10.6 ms CPU per
#   request at 88 req/s; 4 threads = 18.8 ms at 59 req/s);
# * "sync" workers take one connection at a time, so the kernel hands each new request
#   to an idle worker. Threaded workers holding nginx keep-alive connections queued
#   requests behind busy workers while others sat idle (measured with py-spy).
# ~2 workers per CPU, so a worker waiting on the routing service does not idle a core.
# nginx buffers slow clients, which is what makes sync workers safe here.
workers = int(os.environ.get("WEB_CONCURRENCY") or min(multiprocessing.cpu_count() * 2 + 1, 8))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS") or "sync"
threads = int(os.environ.get("GUNICORN_THREADS") or 1)  # only used by the gthread class
timeout = 60  # a cold route waits on OSRM (+ rate-limit slot); cached ones take ~30 ms
graceful_timeout = 30  # finish in-flight requests on deploy / scale-down
# Recycle workers now and then to cap memory growth; jitter avoids restarting all at once.
max_requests = 2000
max_requests_jitter = 200
worker_tmp_dir = "/dev/shm"
forwarded_allow_ips = "*"  # only nginx can reach the container
accesslog = "-"
errorlog = "-"
loglevel = (os.environ.get("LOG_LEVEL") or "info").lower()
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms rid=%({x-request-id}i)s'
