# Locust load test image (Locust pinned).
FROM locustio/locust:2.46.7
WORKDIR /app
COPY --chown=locust:locust *.py docker-entrypoint.sh ./
USER root
RUN chmod +x /app/docker-entrypoint.sh && mkdir -p /app/reports && chmod 777 /app/reports
USER locust
ENTRYPOINT ["/app/docker-entrypoint.sh"]
