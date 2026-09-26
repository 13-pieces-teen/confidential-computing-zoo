# Explicit experiment image only; normal configs/Dockerfile.openviking is unchanged.
FROM ghcr.io/volcengine/openviking@sha256:27d3c97bddbe81f31d2c5af1f31e9d504b5928506c88f559a23faf86358169b7
COPY receiver_audit /opt/argus-audit/receiver_audit
ENV PYTHONPATH=/opt/argus-audit
RUN python -m receiver_audit.startup --argus-verify-upstream
LABEL org.argus.receiver.boundary="asgi_application_read" org.argus.receiver.schema="2"
ENTRYPOINT ["python", "-m", "receiver_audit.startup"]
CMD []
