# MinIO / S3 Object Storage — Dev Environment

This document covers how MinIO is initialised in the bundled dev stack,
what buckets and permissions are required, and how to swap in an external
S3-compatible storage (AWS S3, Google Cloud Storage, Cloudflare R2, etc.).

---

## 1. How the bundled MinIO container works

### Image and startup

`docker-compose.4container.yml` uses the official `minio/minio:latest` image
directly — no custom Dockerfile:

```yaml
minio:
  image: minio/minio:latest
  command: server /data --console-address :9001
  environment:
    MINIO_ROOT_USER:     minioadmin          # or $MINIO_ROOT_USER
    MINIO_ROOT_PASSWORD: minioadmin123       # or $MINIO_ROOT_PASSWORD
  volumes:
    - minio_data:/data
  ports:
    - "19000:9000"    # S3 API (used by backend container)
    - "19001:9001"    # Web console (browser)
```

MinIO persists data to the named volume `minio_data`.  Credentials survive
container restarts.  There is no separate "first boot only" logic — MinIO
starts with whatever data volume is mounted.

### minio-init (one-shot bucket creation)

A separate `minio-init` container runs `infra/minio/init.sh` once and exits:

```yaml
minio-init:
  image: minio/mc:latest
  restart: "no"
  depends_on:
    minio: {condition: service_healthy}
  entrypoint: /bin/sh /init.sh
```

`infra/minio/init.sh`:

```sh
mc alias set myminio http://minio:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}
mc mb --ignore-existing myminio/rag-documents
```

Creates a single bucket; `--ignore-existing` makes it safe to re-run.
All content — documents and model weights — lives under key prefixes within
this one bucket.  There is no separate `rag-models` bucket.

> Documents are stored under the key prefix `documents/<sha256>/`.
> Model weights are stored under `models/models/bert_uncased_L-12_H-768_A-12/`.
> Both share the single `rag-documents` bucket.

### Backend startup bootstrap (`s3_ensure.py`)

Every time the backend container starts, `start.sh` runs
`python /app/s3_ensure.py` before launching uvicorn.

What it does: attempts `head_bucket` — if the bucket is missing (HTTP 404 /
`NoSuchBucket`) it calls `create_bucket`.  All other existing-bucket errors
are treated as success.  Exits non-zero only if credentials are missing.

This means you can skip the `minio-init` container entirely when using an
external S3 — the backend will create the bucket on first start.

---

## 2. Environment variables

All S3 configuration is driven by these backend environment variables:

| Variable | Bundled dev default | Description |
|---|---|---|
| `S3_ENDPOINT_URL` | `http://minio:9000` | Internal service URL. Set empty / unset for real AWS S3. |
| `S3_ACCESS_KEY` | `minioadmin` | Access key ID. Alias: `AWS_ACCESS_KEY_ID`. |
| `S3_SECRET_KEY` | `minioadmin123` | Secret access key. Alias: `AWS_SECRET_ACCESS_KEY`. |
| `S3_BUCKET` | `rag-documents` | Application documents bucket. |
| `S3_REGION` | `us-east-1` | AWS region. Ignored by MinIO. |
| `S3_EXTERNAL_URL` | `http://localhost:19000` | Base URL used in presigned download links returned to the browser. See §4. |

---

## 3. Using an external S3-compatible service

### What to change

Stop the `minio` and `minio-init` services and supply the external credentials
when bringing the stack up.  The `s3_ensure.py` bootstrap will create the
bucket on first start.

> The backend's `depends_on.minio-init` condition will fail unless you also
> remove or override that dependency.  Use `-f` override or bring up services
> individually (see below).

```bash
# Example: bring up only non-MinIO services
OPENAI_API_KEY=... \
S3_ENDPOINT_URL=https://my-minio.example.com \
S3_ACCESS_KEY=myaccesskey \
S3_SECRET_KEY=mysecretkey \
S3_BUCKET=rag-documents \
S3_EXTERNAL_URL=https://my-minio.example.com \
docker compose -f docker-compose.4container.yml \
  up postgres ocr-api backend frontend
```

---

## 4. Platform-specific setup

### Self-hosted MinIO

No extra steps beyond credentials.  `s3_ensure.py` creates the bucket.

```
S3_ENDPOINT_URL = http://<host>:<port>     # or https://
S3_ACCESS_KEY   = <MinIO root user or generated access key>
S3_SECRET_KEY   = <corresponding secret>
S3_EXTERNAL_URL = http://<browser-accessible-host>:<port>
```

The browser-accessible URL (`S3_EXTERNAL_URL`) matters only for presigned
download links surfaced via the `/documents/{id}/download-url` API endpoint.
If the backend and browser are on the same host, `S3_ENDPOINT_URL` and
`S3_EXTERNAL_URL` can be identical.

---

### AWS S3

Set `S3_ENDPOINT_URL` to empty (or leave it unset) so the aioboto3 client
directs requests to the real AWS S3 API.

```
S3_ENDPOINT_URL =              # empty — aioboto3 resolves to AWS endpoints
S3_ACCESS_KEY   = AKIA...      # IAM user or role access key
S3_SECRET_KEY   = ...
S3_REGION       = eu-west-1    # region where the bucket should be created
S3_BUCKET       = my-rag-docs  # globally unique bucket name
S3_EXTERNAL_URL =              # leave empty — AWS presigned URLs are already public
```

**Minimum IAM permissions required** (attach to the IAM user / role):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:HeadBucket",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::my-rag-docs",
        "arn:aws:s3:::my-rag-docs/*"
      ]
    }
  ]
}
```

If you pre-create the bucket yourself and don't want `s3_ensure.py` to need
`s3:CreateBucket`, remove that action from the policy.

**Bucket creation note:** for regions other than `us-east-1`, `s3_ensure.py`
automatically includes `CreateBucketConfiguration.LocationConstraint` in the
`create_bucket` call.

---

### Cloudflare R2

R2 exposes an S3-compatible API.  Account ID and bucket are in the Cloudflare
dashboard.

```
S3_ENDPOINT_URL = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
S3_ACCESS_KEY   = <R2 access key ID>
S3_SECRET_KEY   = <R2 secret access key>
S3_REGION       = auto               # R2 ignores region but must be non-empty
S3_BUCKET       = rag-documents
S3_EXTERNAL_URL = https://pub.<ACCOUNT_ID>.r2.dev  # if public access enabled
                                                    # or your custom domain
```

---

### Google Cloud Storage (interoperability mode)

GCS supports an S3-compatible HMAC-key API.

```
S3_ENDPOINT_URL = https://storage.googleapis.com
S3_ACCESS_KEY   = <HMAC access key>
S3_SECRET_KEY   = <HMAC secret>
S3_REGION       = us-east-1    # placeholder — GCS ignores this
S3_BUCKET       = my-rag-docs
S3_EXTERNAL_URL =              # GCS presigned URLs are already public
```

Create HMAC keys in: Cloud Console → Cloud Storage → Settings → Interoperability.

---

## 5. `S3_EXTERNAL_URL` — when and why

The backend generates presigned download URLs for documents stored in S3.
These URLs must be accessible from the **browser**, not from inside the
Docker network.

| Scenario | `S3_ENDPOINT_URL` (internal) | `S3_EXTERNAL_URL` (browser) |
|---|---|---|
| Local MinIO | `http://minio:9000` | `http://localhost:19000` |
| MinIO behind nginx | `http://minio:9000` | `https://storage.example.com` |
| AWS S3 | *(empty)* | *(empty — AWS URLs already work)* |
| Cloudflare R2 public | `https://<id>.r2.cloudflarestorage.com` | `https://pub.<id>.r2.dev` |

When `S3_EXTERNAL_URL` is empty the backend falls back to `S3_ENDPOINT_URL`
for presigned URL generation.

---

## 6. Object layout inside the bucket

```
rag-documents/
├── documents/
│   └── <sha256-hash>/
│       └── filename.pdf          ← uploaded document
└── models/
    └── models/
        └── bert_uncased_L-12_H-768_A-12/
            ├── config.json
            ├── vocab.txt
            ├── tokenizer.json
            └── bert_model.ckpt.* ← raw checkpoints (model-init source)
```

The `MODEL_S3_KEY_PREFIX` env var (`models/models/bert_uncased_L-12_H-768_A-12`)
controls where model-init looks for weights.  It always reads from `S3_BUCKET`
(i.e. `rag-documents`), so both documents and models share the same bucket.

---

## 7. Verifying the setup

**From the MinIO web console** (bundled MinIO):
```
http://localhost:19001
Login: minioadmin / minioadmin123
```

**From the command line using `aws` CLI or MinIO `mc`:**

```bash
# aws CLI (works for both MinIO and real S3)
AWS_ACCESS_KEY_ID=minioadmin \
AWS_SECRET_ACCESS_KEY=minioadmin123 \
aws --endpoint-url http://localhost:19000 s3 ls s3://rag-documents/

# MinIO mc
mc alias set dev http://localhost:19000 minioadmin minioadmin123
mc ls dev/rag-documents/
mc du dev/rag-documents/
```

**From inside the running backend container:**

```bash
docker exec document-simple-rag-backend-1 python3 - <<'EOF'
import asyncio, aioboto3, os

async def check():
    session = aioboto3.Session(
        aws_access_key_id=os.environ['S3_ACCESS_KEY'],
        aws_secret_access_key=os.environ['S3_SECRET_KEY'],
    )
    async with session.client('s3', endpoint_url=os.environ.get('S3_ENDPOINT_URL') or None) as s3:
        bucket = os.environ['S3_BUCKET']
        resp = await s3.list_objects_v2(Bucket=bucket, MaxKeys=5)
        print(f"Bucket: {bucket}")
        print(f"Objects: {resp.get('KeyCount', 0)} (showing up to 5)")
        for obj in resp.get('Contents', []):
            print(f"  {obj['Key']}  ({obj['Size']:,} bytes)")

asyncio.run(check())
EOF
```
