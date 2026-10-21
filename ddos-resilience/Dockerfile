FROM python:3.12-alpine
WORKDIR /app
COPY flood.py report.py ./
RUN mkdir -p reports && chmod 777 reports
ENTRYPOINT ["python3", "-u", "flood.py"]
