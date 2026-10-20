# k6 load test image: k6 (pinned) + Python 3 for the terminal monitor.
FROM grafana/k6:2.3.0 AS k6

FROM python:3.12-alpine
COPY --from=k6 /usr/bin/k6 /usr/bin/k6
WORKDIR /app
COPY src/ src/
COPY ui/ ui/
COPY monitor.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && mkdir -p reports && chmod 777 reports
ENTRYPOINT ["/app/docker-entrypoint.sh"]
