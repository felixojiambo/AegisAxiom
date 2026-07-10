FROM python:3.11-slim

WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends curl git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install all dependencies. emergentintegrations is provided natively in the Emergent
# platform and is not available on PyPI, so we allow it to fail gracefully here.
# The application handles the missing package with try/except guards at runtime.
RUN grep -v 'emergentintegrations' requirements.txt > requirements.docker.txt && \
    pip install --no-cache-dir -r requirements.docker.txt && \
    rm requirements.docker.txt

COPY . .

# Install Node.js for BPMN node_helpers (bpmn-moddle validation)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
RUN cd /app/backend/services/bpmn/node_helpers && npm install --production

RUN mkdir -p /app/backend/uploads

EXPOSE 8001

# DEV_MODE=true  → --reload for hot-reload with mounted volumes
# DEV_MODE=false → --workers 2 for production throughput
ARG DEV_MODE=true
ENV DEV_MODE=${DEV_MODE}

CMD if [ "$DEV_MODE" = "true" ]; then \
      uvicorn server:app --host "::" --port 8001 --reload; \
    else \
      uvicorn server:app --host "::" --port 8001 --workers 2; \
    fi
