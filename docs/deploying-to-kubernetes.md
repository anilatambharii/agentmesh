# Deploying AgentMesh to Kubernetes

AgentMesh can be deployed as a sidecar container or as a standalone proxy service in Kubernetes.

## Option 1 — Sidecar (Recommended)

Inject AgentMesh as a sidecar proxy alongside your agent workloads. All LLM traffic flows through the sidecar.

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-agent-app
spec:
  template:
    spec:
      containers:
        - name: agent-app
          image: my-agent:latest
          env:
            - name: ANTHROPIC_BASE_URL
              value: "http://localhost:8080"   # point at sidecar

        - name: agentmesh-proxy               # sidecar
          image: python:3.12-slim
          command: ["agentmesh", "proxy", "--port", "8080", "--policy", "/config/policy.yaml"]
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: policy-config
              mountPath: /config
          resources:
            requests: { cpu: "100m", memory: "128Mi" }
            limits:   { cpu: "500m", memory: "512Mi" }

      volumes:
        - name: policy-config
          configMap:
            name: agentmesh-policy
```

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentmesh-policy
data:
  policy.yaml: |
    version: "1.0"
    policies:
      - name: production-agents
        budget:
          daily_tokens: 1_000_000
          monthly_usd: 3_000
          hard_stop: true
        circuit_breaker:
          max_iterations: 25
        compliance:
          frameworks: [eu-ai-act, soc2]
```

## Option 2 — Standalone Proxy Service

Deploy as a shared gateway for all agent workloads in a namespace:

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: agentmesh
spec:
  selector:
    app: agentmesh
  ports:
    - port: 8080
      targetPort: 8080
```

All agents then point to `http://agentmesh:8080` as their LLM base URL.

## Secrets Management

Store signing keys and API keys as Kubernetes secrets:

```bash
kubectl create secret generic agentmesh-secrets \
  --from-literal=audit-signing-key="your-ed25519-key-hex" \
  --from-literal=anthropic-api-key="sk-ant-..."
```

```yaml
env:
  - name: AGENTMESH_SIGNING_KEY
    valueFrom:
      secretKeyRef:
        name: agentmesh-secrets
        key: audit-signing-key
```

## Health Checks

The AgentMesh proxy exposes a health endpoint at `GET /health`:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 3
  periodSeconds: 5
```

## Horizontal Scaling

AgentMesh proxy is stateless by default. Scale horizontally:

```yaml
spec:
  replicas: 3
```

For shared budget enforcement across replicas, use the Redis budget backend (v0.3 roadmap):

```python
# Coming in v0.3
from agentmesh.budget.redis_enforcer import RedisBudgetEnforcer
mesh = AgentMesh(budget_backend=RedisBudgetEnforcer(redis_url="redis://redis:6379"))
```

## Kubernetes Operator (Roadmap v0.3)

A Kubernetes operator for cluster-wide policy management is planned for v0.3:

```yaml
apiVersion: agentmesh.io/v1
kind: AgentPolicy
metadata:
  name: production-policy
  namespace: ai-agents
spec:
  budget:
    dailyTokens: 10_000_000
    monthlyUSD: 5000
    hardStop: true
  circuitBreaker:
    maxIterations: 25
  applyTo:
    namespaces: ["ai-agents", "ml-platform"]
```
