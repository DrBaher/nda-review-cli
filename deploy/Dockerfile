# Sandboxed read-only demo for nda-review-cli.
# Stdlib-only: no pip install needed for the CLI itself; the web service is
# also stdlib-only Python, so this image stays small.

FROM python:3.11-slim

# Non-root user for the running process
RUN useradd -m -u 10001 demo

WORKDIR /app

# Copy CLI + web demo. Keep .dockerignore tight so the build is reproducible.
COPY --chown=demo:demo nda_review_cli.py rule_engine.py ./
COPY --chown=demo:demo config/default-policy.json config/scoring-profiles.json config/llm.json.example ./config/
COPY --chown=demo:demo templates/ ./templates/
COPY --chown=demo:demo tests/fixtures/sample_nda.txt ./tests/fixtures/sample_nda.txt
COPY --chown=demo:demo web/ ./web/

# Make the CLI executable in case it's invoked directly
RUN chmod +x nda_review_cli.py

USER demo

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    NDA_DEMO_SANDBOX=/tmp/nda-demo-sandbox \
    NDA_DEMO_SESSION_TTL=1800 \
    NDA_DEMO_MAX_SESSIONS=200 \
    NDA_DEMO_RATE_LIMIT=30 \
    NDA_DEMO_SUBPROCESS_TIMEOUT=30

EXPOSE 8080

# Healthcheck — Railway/Fly use this for readiness
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)" || exit 1

CMD ["python3", "web/server.py"]
