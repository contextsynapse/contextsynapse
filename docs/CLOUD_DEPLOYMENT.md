# Cloud Deployment — 5-10 Users, 5 Concurrent

## Architecture

```
                    ┌──────────────┐
                    │   CloudFlare  │  CDN + SSL
                    │   or ALB/GLB  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐ ┌───▼───┐ ┌─────▼──────┐
        │  Frontend  │ │  API  │ │  Pipeline  │
        │  (Static)  │ │Server │ │  Worker    │
        │  S3/GCS    │ │       │ │            │
        └───────────┘ └───┬───┘ └─────┬──────┘
                          │           │
                    ┌─────▼───────────▼─────┐
                    │      Redis             │
                    │  (graphs + cache +     │
                    │   pub/sub + queues)     │
                    └────────────────────────┘
```

## Option A: AWS (Recommended for cost)

### Compute
| Service | Spec | Purpose | Monthly Cost |
|---------|------|---------|-------------|
| EC2 t3.medium | 2 vCPU, 4GB RAM | API Server (uvicorn) | $30 |
| EC2 t3.small | 2 vCPU, 2GB RAM | Pipeline Worker (continuous) | $15 |

### Data
| Service | Spec | Purpose | Monthly Cost |
|---------|------|---------|-------------|
| ElastiCache Redis | cache.t3.micro, 0.5GB | Graph storage, cache, pub/sub | $13 |
| S3 | ~1GB | Frontend static files | $0.02 |

### Extras
| Service | Purpose | Monthly Cost |
|---------|---------|-------------|
| ALB | Load balancer + SSL | $16 |
| Route 53 | DNS | $0.50 |
| CloudWatch | Logs + monitoring | $5 |
| Secrets Manager | API keys (OpenAI, Yahoo) | $1 |

**AWS Total: ~$80/month**

### Cheaper AWS option (single instance)
| Service | Spec | Monthly Cost |
|---------|------|-------------|
| EC2 t3.medium | API + Pipeline on same box | $30 |
| ElastiCache Redis | cache.t3.micro | $13 |
| S3 + CloudFront | Frontend | $1 |
| **Total** | | **$44/month** |

## Option B: GCP

### Compute
| Service | Spec | Purpose | Monthly Cost |
|---------|------|---------|-------------|
| Cloud Run | 2 vCPU, 4GB | API Server (auto-scales to 0) | $20-40 |
| Compute Engine e2-small | 2 vCPU, 2GB | Pipeline Worker | $12 |

### Data
| Service | Spec | Purpose | Monthly Cost |
|---------|------|---------|-------------|
| Memorystore Redis | Basic, 1GB | Graph storage | $35 |
| Cloud Storage | ~1GB | Frontend static | $0.02 |

### Extras
| Service | Purpose | Monthly Cost |
|---------|---------|-------------|
| Cloud Load Balancing | SSL + routing | $18 |
| Cloud DNS | DNS | $0.20 |
| Cloud Logging | Logs | $0 (free tier) |

**GCP Total: ~$85/month**

### GCP advantage
- Cloud Run scales to 0 when no users → saves money during off-hours
- But Memorystore Redis is more expensive than AWS ElastiCache

## Recommendation: AWS Single Instance

For 5-10 users, a single EC2 instance is plenty:

```
EC2 t3.medium (4GB RAM)
├── uvicorn (API server) — port 8000
├── pipeline worker (background thread)
├── Redis (localhost) — install directly on instance
├── Qdrant (localhost) — for vector embeddings
└── nginx — reverse proxy + serve frontend static

Total: $30/month + domain name
```

### Why this works for 5 users:
- uvicorn handles 100+ concurrent requests easily
- Redis on localhost = zero network latency
- Pipeline worker runs as a systemd service
- 4GB RAM handles ~50,000 graph nodes comfortably
- t3.medium gets burst CPU credits for pipeline spikes

### Setup commands:
```bash
# Ubuntu 22.04 on EC2
sudo apt update && sudo apt install -y redis-server nginx python3.12

# Install app
git clone <repo>
cd qgraph-app
pip install -e .

# Redis config
sudo systemctl enable redis-server

# API server (systemd service)
# /etc/systemd/system/qgraph-api.service
[Service]
ExecStart=/usr/bin/python3 -m uvicorn contextsynapse.api.api:app --host 0.0.0.0 --port 8000
WorkingDirectory=/opt/qgraph-app
EnvironmentFile=/opt/qgraph-app/.env
Restart=always

# Pipeline worker (systemd service)
# /etc/systemd/system/qgraph-pipeline.service
[Service]
ExecStart=/usr/bin/python3 -m plugins.stock_analysis.pipelines --continuous
WorkingDirectory=/opt/qgraph-app
EnvironmentFile=/opt/qgraph-app/.env
Restart=always

# Nginx reverse proxy
server {
    listen 443 ssl;
    server_name app.qgraph.ai;

    location / {
        root /opt/qgraph-app/frontend/build;
        try_files $uri /index.html;  # SPA routing
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
    }

    location /graph/ {
        proxy_pass http://127.0.0.1:8000/graph/;
    }

    location /dashboard/ {
        # API calls go to backend
        proxy_pass http://127.0.0.1:8000/dashboard/;
    }

    location /intelligence/ {
        proxy_pass http://127.0.0.1:8000/intelligence/;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## Scaling Later (50+ users)

When you grow beyond 10 users:
1. Move Redis to ElastiCache/Memorystore (managed)
2. Add a second API instance behind ALB
3. Move pipeline to a dedicated worker instance
4. Add CloudFront/CDN for frontend
5. Consider ECS/Fargate for container orchestration

## Required Environment Variables
```
AICONTEXTDB_REDIS_URL=redis://localhost:6379/0
AICONTEXTDB_ADMIN_KEY=<generate>
AICONTEXTDB_JWT_SECRET=<generate>
OPENAI_API_KEY=<your key>
AICONTEXTDB_CORS_ORIGINS=https://app.qgraph.ai
```

## Cost Summary

| Scale | Infra | Monthly Cost |
|-------|-------|-------------|
| 5 users (minimal) | 1 EC2 + local Redis | $30 |
| 10 users (comfortable) | 1 EC2 + ElastiCache | $45 |
| 50 users | 2 EC2 + ElastiCache + ALB | $120 |
| 100+ users | ECS/Fargate + managed services | $300+ |

**Bottom line: $30-45/month to start. AWS. Single t3.medium instance with Redis installed locally. Nginx as reverse proxy (which also fixes the SPA routing issue permanently).**
