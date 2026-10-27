FROM python:3.12-slim
WORKDIR /relay
COPY relay.py /relay/relay.py
USER 65534:65534
ENTRYPOINT ["python", "/relay/relay.py"]
