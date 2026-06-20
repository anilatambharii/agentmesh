FROM python:3.11-slim

WORKDIR /app

# Install core deps first for layer caching
COPY pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir "agentmesh-proxy[semantic]==0.3.2"

# Copy source (for SDK / CLI usage on top of installed package)
COPY agentmesh/ ./agentmesh/
COPY examples/ ./examples/

EXPOSE 8080

ENV AGENTMESH_HOST=0.0.0.0
ENV AGENTMESH_PORT=8080

CMD ["python", "-m", "agentmesh.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
