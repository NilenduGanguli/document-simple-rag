**ENTERPRISE RAG PIPELINE**
Architecture & System Design Document

*Ingestion · Chunking · Embedding · Retrieval*

  **Revision 2.0 — OpenShift CPU-Only Deployment**

| Attribute         | Detail                                                                      |
| ----------------- | --------------------------------------------------------------------------- |
| Document Type     | System Architecture Design                                                  |
| Version           | 2.0 (OpenShift CPU-Only Revision)                                           |
| Supersedes        | Version 1.0 (GPU / Kubernetes)                                              |
| Classification    | Internal — Confidential                                                    |
| Target Scale      | 1 TB / 100,000 Documents per Hour                                           |
| Deployment Target | Red Hat OpenShift — CPU-only nodes, no GPU                                 |
| Primary Stack     | Python · RabbitMQ · Redis · PostgreSQL · PGVector · S3 · ONNX Runtime |
| ML Models         | BERT Multilingual (INT8 ONNX) · BERT Cross-Encoder (INT8 ONNX)             |
| Status            | Draft — For Architecture Review                                            |

# **Table of Contents**

1\.  Executive Summary & Revision Notes

2\.  System Architecture Overview

3\.  Technology Stack (OpenShift CPU-Only)

4\.  Data Models & Database Schema

5\.  Service 1: Ingest API

6\.  Service 2: Ingestion Worker

7\.  Service 3: Ingestion Router

8\.  Service 4: OCR Service

9\.  Service 5: Chunking Engine

10\. Service 6: Embedding Service (CPU / ONNX)

11\. Service 7: Retrieval API

12\. Retrieval Pipeline — Deep Dive

13\. Queue Architecture (RabbitMQ)

14\. Cache Strategy (Redis)

15\. Storage Strategy

16\. Security & Resilience

17\. Observability & Monitoring

18\. OpenShift Deployment — Full Specification

19\. BERT Model Loading in OpenShift Production Pods

20\. Performance Benchmarks & SLAs (CPU-Only)

21\. Future Extensibility

# **1\. Executive Summary & Revision Notes**

*⚠  This is Revision 2.0 of the Enterprise RAG Pipeline Design Document. The fundamental pipeline architecture, data models, and retrieval algorithms remain unchanged from v1.0. This revision replaces ALL GPU-dependent components with CPU-optimised equivalents, replaces Kubernetes with Red Hat OpenShift, and adds a dedicated section (Section 19\) covering BERT model loading strategy for OpenShift production pods.*

The system ingests up to 100,000 documents (≈ 1 TB) per hour using a seven-service Python microservices architecture connected via RabbitMQ queues. BERT inference runs exclusively on CPU using INT8-quantised ONNX models served through ONNX Runtime with OpenMP thread parallelism. To compensate for the absence of GPU acceleration, the embedding and retrieval services are designed with aggressive batching, model-instance pooling, async pre-fetching, and horizontal pod autoscaling driven by queue depth metrics.

### **What Changed from v1.0**

| Area               | v1.0 (GPU / Kubernetes)           | v2.0 (CPU / OpenShift)            |
| ------------------ | --------------------------------- | --------------------------------- |
| Inference Runtime  | PyTorch FP16\+ NVIDIA Triton      | ONNX Runtime 1.18 INT8\+ OpenMP   |
| Compute            | GPU (A100/H100) per embedding pod | CPU-only nodes; no GPU toleration |
| Embedding batch    | 64 chunks / call (GPU VRAM)       | 16 chunks / call (RAM bandwidth)  |
| Embedding latency  | \~300 ms per batch (GPU FP16)     | \~900 ms per batch (CPU INT8)     |
| Embedding pods     | 10 GPU pods                       | 40 CPU pods (autoscaled to 80\)   |
| Model serving      | Triton Inference Server           | In-process ONNX Runtime pool      |
| Orchestration      | Kubernetes\+ HPA                  | OpenShift DeploymentConfig\+ HPA  |
| Ingress            | Kubernetes Ingress\+ cert-manager | OpenShift Route\+ Let's Encrypt   |
| Security           | Pod Security Standards            | OpenShift SCC (restricted-v2)     |
| Model distribution | CloudFront CDN → container image | S3 Init Container\+ PVC mount     |
| New section        | —                                | Section 19: BERT Model Loading    |

# **2\. System Architecture Overview**

## **2.1 High-Level Data Flow**

The pipeline operates across two fully decoupled planes sharing only the database layer. The Ingestion Plane is write-heavy and CPU-bound; the Retrieval Plane is read-heavy and latency-sensitive. Both planes run exclusively on OpenShift CPU worker nodes.

### **Ingestion Plane — Write Path**

1. Client POSTs document to Ingest API (REST/HTTPS via OpenShift Route)
2. API saves raw file to S3 (MinIO on-cluster or AWS S3), creates parent\_document record in PostgreSQL, returns document\_id
3. API publishes ingestion task to RabbitMQ ingestion\_queue (msgpack, persistent, priority)
4. Ingestion Worker consumes task; passes document through Ingestion Router
5. Router extracts digital text (PyMuPDF) and separates embedded/scanned images
6. Images dispatched to OCR Service via ocr\_queue (async, non-blocking)
7. Preprocessor merges OCR text \+ extracted text, cleans and normalises
8. Chunking Engine splits text into chunks, assigns chunk\_ids, stores in PostgreSQL
9. Chunk IDs published to embedding\_queue in micro-batches of 16
10. Embedding Service consumes batches, runs INT8 BERT ONNX, writes vectors to PGVector

### **Retrieval Plane — Read Path**

11. Client POSTs query to Retrieval API (REST/HTTPS via OpenShift Route)
12. API logs request to retrieval\_audit table; checks Redis full-result cache
13. Query Preprocessor runs BERT NER (INT8 ONNX) for entity extraction
14. Dense HNSW search (BERT bi-encoder INT8 ONNX) runs against PGVector read replica
15. Sparse BM25 search runs against in-memory index
16. Reciprocal Rank Fusion (RRF) merges ranked lists
17. MMR re-ranks for diversity
18. BERT cross-encoder (INT8 ONNX) performs final precision re-ranking
19. Result aggregation applies k-chunks or n-documents logic
20. Response returned with chunks, metadata, parent paths, and full audit trail

## **2.2 Service Topology on OpenShift**

| Service           | Type                  | Min Pods | Max Pods | Queue In         | Queue Out                    |
| ----------------- | --------------------- | -------- | -------- | ---------------- | ---------------------------- |
| ingest-api        | Deployment\+ Route    | 3        | 10       | —               | ingestion\_queue             |
| ingestion-worker  | Deployment (consumer) | 25       | 60       | ingestion\_queue | ocr\_queue, embedding\_queue |
| ocr-service       | Deployment\+ Service  | 8        | 20       | ocr\_queue       | —                           |
| embedding-service | Deployment (consumer) | 40       | 80       | embedding\_queue | —                           |
| retrieval-api     | Deployment\+ Route    | 5        | 20       | —               | —                           |
| rabbitmq          | StatefulSet (quorum)  | 3        | 3        | —               | —                           |
| redis-cluster     | StatefulSet (cluster) | 6        | 6        | —               | —                           |
| postgresql        | StatefulSet           | 1+2R     | —       | —               | —                           |

# **3\. Technology Stack (OpenShift CPU-Only)**

**◈ REVISED — OpenShift CPU-Only:  GPU stack removed; ONNX Runtime INT8 replaces PyTorch FP16 \+ Triton; OpenShift replaces Kubernetes.**

| Layer                | Technology                        | Version      | Purpose                                       |
| -------------------- | --------------------------------- | ------------ | --------------------------------------------- |
| API Framework        | FastAPI                           | 0.111+       | Async REST with Pydantic v2 validation        |
| Queue Broker         | RabbitMQ                          | 3.13+        | Durable AMQP queues; quorum mode; DLQ         |
| Cache                | Redis                             | 7.2+ Cluster | Embedding cache, session state, rate limiting |
| Relational DB        | PostgreSQL                        | 16+          | Document metadata, chunk records, audit logs  |
| Vector DB            | PGVector                          | 0.7+         | HNSW\+ IVFFlat vector indexes                 |
| Object Storage       | AWS S3 / OpenShift MinIO          | —           | Raw document\+ model artifact storage         |
| ML Inference Runtime | ONNX Runtime (CPU)                | 1.18+        | INT8 quantised BERT inference on CPU          |
| Thread Parallelism   | OpenMP via ONNX Runtime           | —           | Intra-op parallelism for ONNX CPU execution   |
| BERT Embedding       | bert-base-multilingual-cased INT8 | HuggingFace  | Bi-encoder dense retrieval                    |
| BERT Cross-Encoder   | bert-base-multilingual-cased INT8 | HuggingFace  | Cross-encoder re-ranking                      |
| BERT NER             | bert-base-multilingual-cased INT8 | HuggingFace  | Query entity extraction                       |
| ONNX Quantisation    | optimum\+ onnxruntime-tools       | 1.20+        | Dynamic INT8 quantisation of BERT weights     |
| OCR Engine           | Tesseract\+ pytesseract           | 5.3+         | Image-to-text extraction                      |
| PDF Parsing          | PyMuPDF (fitz)                    | 1.24+        | Native PDF text\+ image extraction            |
| BM25                 | rank\_bm25                        | 0.2+         | Sparse keyword retrieval                      |
| Serialisation        | msgpack                           | 1.0+         | Fast binary queue payloads                    |
| AMQP Client          | aio-pika                          | 9.4+         | Async RabbitMQ client                         |
| Redis Client         | redis-py (async)                  | 5.0+         | Async Redis pipeline operations               |
| Tracing              | OpenTelemetry\+ Jaeger            | —           | Distributed request tracing                   |
| Metrics              | Prometheus\+ Grafana              | —           | System and business metrics                   |
| Orchestration        | Red Hat OpenShift                 | 4.14+        | Container platform (CPU nodes only)           |
| CI/CD                | OpenShift Pipelines (Tekton)      | —           | Build, test, promote pipeline                 |
| Image Registry       | OpenShift Internal Registry       | —           | Container image storage                       |
| Service Mesh         | Red Hat Service Mesh (Istio)      | 2.5+         | mTLS, traffic management, observability       |

# **4\. Data Models & Database Schema**

Schemas are unchanged from v1.0. Reproduced here for completeness.

## **4.1 parent\_documents**

CREATE TABLE parent\_documents (
    parent\_document\_id   UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),
    filename             TEXT NOT NULL,
    s3\_bucket            TEXT NOT NULL,
    s3\_key               TEXT NOT NULL,
    s3\_uri               TEXT GENERATED ALWAYS AS
    ('s3://' || s3\_bucket || '/' || s3\_key) STORED,
    file\_size\_bytes      BIGINT,
    mime\_type            TEXT DEFAULT 'application/pdf',
    page\_count           INT,
    has\_text             BOOLEAN DEFAULT FALSE,
    has\_images           BOOLEAN DEFAULT FALSE,
    language\_detected    TEXT,
    status               TEXT DEFAULT 'pending'
    CHECK (status IN ('pending','ingesting','chunking','embedding','ready','failed')),
    error\_message        TEXT,
    retry\_count          INT DEFAULT 0,
    source\_metadata      JSONB DEFAULT '{}',
    created\_at           TIMESTAMPTZ DEFAULT now(),
    updated\_at           TIMESTAMPTZ DEFAULT now(),
    completed\_at         TIMESTAMPTZ
);
CREATE INDEX idx\_pd\_status  ON parent\_documents(status);
CREATE INDEX idx\_pd\_created ON parent\_documents(created\_at DESC);

## **4.2 chunks**

CREATE TABLE chunks (
    chunk\_id             UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),
    parent\_document\_id   UUID NOT NULL REFERENCES parent\_documents(parent\_document\_id) ON DELETE CASCADE,
    chunk\_index          INT NOT NULL,
    chunk\_text           TEXT NOT NULL,
    char\_start           INT,  char\_end INT,  page\_number INT,
    source\_type          TEXT DEFAULT 'text' CHECK (source\_type IN ('text','ocr','mixed')),
    token\_count          INT,  language TEXT,
    chunk\_metadata       JSONB DEFAULT '{}',
    embedding\_status     TEXT DEFAULT 'pending'
    CHECK (embedding\_status IN ('pending','processing','done','failed')),
    created\_at           TIMESTAMPTZ DEFAULT now(),
    updated\_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx\_chunks\_parent ON chunks(parent\_document\_id);
CREATE INDEX idx\_chunks\_status ON chunks(embedding\_status);
CREATE INDEX idx\_chunks\_fts    ON chunks USING gin(to\_tsvector('simple', chunk\_text));

## **4.3 chunk\_embeddings (PGVector)**

CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE chunk\_embeddings (
    embedding\_id       UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),
    chunk\_id           UUID NOT NULL REFERENCES chunks(chunk\_id) ON DELETE CASCADE,
    parent\_document\_id UUID NOT NULL,
    embedding          vector(768),\-- BERT multilingual dim=768
    model\_name         TEXT NOT NULL,
    model\_version      TEXT,
    created\_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx\_emb\_hnsw ON chunk\_embeddings
    USING hnsw (embedding vector\_cosine\_ops) WITH (m=32, ef\_construction=200);
CREATE INDEX idx\_emb\_ivfflat ON chunk\_embeddings
    USING ivfflat (embedding vector\_cosine\_ops) WITH (lists=500);

## **4.4 retrieval\_audit**

CREATE TABLE retrieval\_audit (
    audit\_id          UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),
    query\_raw         TEXT NOT NULL,  query\_processed TEXT,
    entities\_detected JSONB DEFAULT '\[\]',  query\_embedding vector(768),
    retrieval\_mode    TEXT CHECK (retrieval\_mode IN ('k\_chunks','n\_documents')),
    k\_requested INT, n\_requested INT,
    dense\_candidates  JSONB DEFAULT '\[\]',  sparse\_candidates JSONB DEFAULT '\[\]',
    rrf\_scores        JSONB DEFAULT '\[\]',  mmr\_selected      JSONB DEFAULT '\[\]',
    final\_ranked      JSONB DEFAULT '\[\]',
    latency\_ms        INT,  client\_ip INET,  api\_key\_hash TEXT,
    created\_at        TIMESTAMPTZ DEFAULT now()
);

# **5\. Service 1: Ingest API**

## **5.1 Overview**

Unchanged from v1.0. The Ingest API is a thin FastAPI gateway. On OpenShift it is exposed via an OpenShift Route with TLS edge termination handled by the OpenShift router (HAProxy). No GPU requirements. Runs on standard CPU worker nodes under the restricted-v2 SCC.

## **5.2 Endpoints**

| Method | Path                                    | Description                     | Auth    |
| ------ | --------------------------------------- | ------------------------------- | ------- |
| POST   | /api/v1/documents/ingest                | Upload document for processing  | API Key |
| GET    | /api/v1/documents/{document\_id}        | Get document status\+ metadata  | API Key |
| GET    | /api/v1/documents/{document\_id}/chunks | List chunks for a document      | API Key |
| DELETE | /api/v1/documents/{document\_id}        | Soft-delete document and chunks | API Key |
| GET    | /api/v1/health                          | Liveness\+ readiness probe      | None    |

## **5.3 Processing Steps**

21. Validate MIME type (application/pdf), reject others with 422
22. Validate file size (max configurable via env, e.g. 500 MB)
23. Compute SHA-256 → Redis deduplication check
24. If cache hit: return existing document\_id (idempotent)
25. Generate parent\_document\_id (UUID v4)
26. Streaming multipart upload to S3 via aioboto3 (never buffer full file in memory)
27. Insert parent\_documents row status='pending' via asyncpg
28. Publish msgpack task to RabbitMQ ingestion\_queue
29. Cache SHA-256 → document\_id in Redis TTL=7d
30. Return 202 Accepted with {document\_id, status}

# **6\. Service 2: Ingestion Worker**

## **6.1 Concurrency Model — CPU-Optimised**

**◈ REVISED — OpenShift CPU-Only:  Coroutine count adjusted for CPU-only; no GPU CUDA context overhead.**

Each Ingestion Worker pod runs 6 async consumer coroutines (up from 4 in v1.0). Because there is no GPU CUDA context overhead and S3 download \+ PostgreSQL writes dominate the time budget, more coroutines per pod improve CPU utilisation without contention. With 25 pods × 6 coroutines \= 150 concurrent processing streams baseline.

| Parameter              | v1.0 (GPU) | v2.0 (CPU / OpenShift)        |
| ---------------------- | ---------- | ----------------------------- |
| Coroutines per pod     | 4          | 6                             |
| Baseline pods          | 10         | 25                            |
| Max pods (HPA)         | 30         | 60                            |
| Prefetch count         | 1          | 1 (unchanged — backpressure) |
| Concurrent streams     | 40         | 150 (baseline) / 360 (max)    |
| CPU request per pod    | 2 CPU      | 4 CPU                         |
| Memory request per pod | 2 GB       | 3 GB                          |

## **6.2 Main Processing Loop**

async def process\_document(message: aio\_pika.IncomingMessage):
    async with message.process(requeue=False):
    payload\= msgpack.unpackb(message.body)
    doc\_id  \= payload\['parent\_document\_id'\]
    try:
    await update\_status(doc\_id, 'ingesting')
    pdf\_bytes      \= await s3\_client.download(payload\['s3\_key'\])
    routing\_result \= await ingestion\_router.route(pdf\_bytes, doc\_id)
    if routing\_result.images:
    ocr\_results \= await asyncio.gather(
    \*\[dispatch\_ocr(img) for img in routing\_result.images\]
    )
    routing\_result.merge\_ocr(ocr\_results)
    clean\_text \= preprocessor.clean(routing\_result.full\_text)
    chunks\= chunking\_engine.chunk(clean\_text, doc\_id)
    chunk\_ids  \= await chunk\_repo.bulk\_insert(chunks)
    \# Publish in micro-batches of 16 (CPU embedding batch size)
    await embedding\_queue.publish\_batch(chunk\_ids, batch\_size=16)
    await update\_status(doc\_id, 'chunking')
    except Exception as e:
    await handle\_failure(doc\_id, e, payload)

## **6.3 Retry & Dead Letter Strategy**

| Retry Attempt | Delay      | Action                                                            |
| ------------- | ---------- | ----------------------------------------------------------------- |
| 1             | 30 seconds | Requeue to ingestion\_queue; increment retry\_count header        |
| 2             | 5 minutes  | Requeue to ingestion\_queue                                       |
| 3             | 30 minutes | Requeue to ingestion\_queue                                       |
| \> 3          | Immediate  | Route to ingestion\_dlq; update DB status='failed'; alert on-call |

# **7\. Service 3: Ingestion Router**

Unchanged from v1.0. In-process library module within the Ingestion Worker. Inspects each PDF page for text density vs image content and routes accordingly. No GPU dependency.

class IngestionRouter:
    def\_\_init\_\_(self):
    self.TEXT\_DENSITY\_THRESHOLD \= 0.05  \# chars per pixel^2
    async def route(self, pdf\_bytes: bytes, doc\_id: str) \-\> RoutingResult:
    doc\= fitz.open(stream=pdf\_bytes, filetype='pdf')
    result\= RoutingResult(document\_id=doc\_id)
    for page\_num, page in enumerate(doc):
    text\_blocks  \= page.get\_text('blocks')
    text\_content \= ' '.join(\[b\[4\] for b in text\_blocks if b\[6\]==0\])
    text\_density \= len(text\_content.strip()) / max(page.rect.area, 1\)
    images\= page.get\_images(full=True)
    if text\_density \> self.TEXT\_DENSITY\_THRESHOLD:
    result.add\_text\_page(page\_num, text\_content)
    elif images:
    for img\_meta in images:
    img\_bytes \= doc.extract\_image(img\_meta\[0\])\['image'\]
    result.add\_image(page\_num, img\_bytes, img\_meta)
    else:
    pix\= page.get\_pixmap(dpi=300)
    result.add\_image(page\_num, pix.tobytes(), is\_full\_page=True)
    return result

# **8\. Service 4: OCR Service**

## **8.1 Overview**

Unchanged functionally from v1.0. Runs Tesseract on CPU — already CPU-native, no changes required for OpenShift. The OpenShift SCC must allow the Tesseract data directory to be writable; solved via an emptyDir volume mount at /tmp/tessdata.

## **8.2 OpenShift SCC Considerations for OCR**

* Run as non-root UID (e.g. 1001\) — set in Dockerfile with USER 1001
* ReadOnlyRootFilesystem: true — mount /tmp as emptyDir for Tesseract temp files
* Tessdata language packs stored in a ConfigMap volume or in the container image
* TESSDATA\_PREFIX env var points to the mounted ConfigMap path

\# OpenShift-compatible Tesseract environment
env:
  \- name: TESSDATA\_PREFIX
    value: /app/tessdata\# Baked into image at build time
  \- name: OMP\_THREAD\_LIMIT  \# Tesseract OpenMP thread cap per pod
    value: '2'
volumeMounts:
  \- name: tmp-vol
    mountPath: /tmp\# Tesseract needs writable /tmp
volumes:
  \- name: tmp-vol
    emptyDir: {}

# **9\. Service 5: Chunking Engine**

Unchanged from v1.0. In-process library with strategy-pattern registry. CPU-only by design. The BERT tokenizer used for token counting is loaded once at startup and is not the ONNX inference model — it is the HuggingFace tokenizer (CPU, fast, no GPU dependency).

| Strategy     | Class                      | Status  | Description                                                    |
| ------------ | -------------------------- | ------- | -------------------------------------------------------------- |
| recursive    | RecursiveCharacterSplitter | Active  | Default; separator-hierarchy splits with 512-token BERT chunks |
| sentence     | SentenceSplitter           | Planned | NLTK sentence boundaries                                       |
| semantic     | SemanticChunker            | Planned | BERT cosine boundary detection (CPU ONNX)                      |
| fixed\_token | FixedTokenSplitter         | Planned | Hard token count splits                                        |
| structure    | DocumentStructureChunker   | Planned | Heading/section-aware splitting                                |

# **10\. Service 6: Embedding Service (CPU / ONNX)**

**◈ REVISED — OpenShift CPU-Only:  Entirely redesigned for CPU inference. PyTorch \+ Triton replaced by ONNX Runtime INT8 \+ model-instance pool.**

## **10.1 BERT CPU Inference Strategy**

On CPU, BERT inference latency is dominated by matrix multiplications across 12 transformer layers. Three techniques are combined to minimise latency and maximise throughput on standard CPU nodes: INT8 dynamic quantisation (reduces weight size 4×, enables INT8 SIMD via AVX-512), ONNX Runtime graph optimisations (operator fusion, constant folding), and parallel model instance pooling (one ONNX session per physical core subset).

## **10.2 Model Quantisation Pipeline (Build-Time)**

\# Run once at model build time; output stored in S3 and baked into image
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer
import onnxruntime as ort
from onnxruntime.quantization import quantize\_dynamic, QuantType

\# Step 1: Export BERT to ONNX (FP32)
model \= ORTModelForFeatureExtraction.from\_pretrained(
    'bert-base-multilingual-cased',
    export=True
)
model.save\_pretrained('/models/bert-multilingual-fp32')

\# Step 2: Dynamic INT8 quantisation (weights only, activations stay FP32)
\# Dynamic quantisation is preferred over static for variable-length text
quantize\_dynamic(
    model\_input  \= '/models/bert-multilingual-fp32/model.onnx',
    model\_output \= '/models/bert-multilingual-int8/model.onnx',
    weight\_type  \= QuantType.QInt8,
    \# Quantise all MatMul and Attention operators
    op\_types\_to\_quantize \= \['MatMul', 'Attention'\],
    per\_channel          \= True,   \# Better accuracy than per-tensor
    reduce\_range         \= True,   \# Required for AVX2 compatibility
    optimize\_model       \= True    \# Run ORT graph optimiser first
)

\# Result: model.onnx shrinks from \~700 MB (FP32) to \~180 MB (INT8)
\# Inference speedup on CPU: \~2-3x vs FP32 with \<1% accuracy loss on BEIR

## **10.3 ONNX Runtime Session Configuration**

import onnxruntime as ort

def create\_ort\_session(model\_path: str, intra\_threads: int) \-\> ort.InferenceSession:
    '''
    intra\_op\_num\_threads: threads for a single operator (MatMul parallelism)
    inter\_op\_num\_threads: threads for running ops in parallel (graph-level)
    On a 16-core node running 4 model instances:
    intra\= 4 threads each \-\> saturates 16 cores cleanly
    '''
    opts\= ort.SessionOptions()
    opts.intra\_op\_num\_threads        \= intra\_threads
    opts.inter\_op\_num\_threads        \= 1
    opts.execution\_mode              \= ort.ExecutionMode.ORT\_SEQUENTIAL
    opts.graph\_optimization\_level    \= ort.GraphOptimizationLevel.ORT\_ENABLE\_ALL
    opts.enable\_cpu\_mem\_arena        \= True
    opts.enable\_mem\_pattern          \= True
    \# Enable MLAS (Microsoft BLAS) kernel with AVX-512 when available
    opts.add\_session\_config\_entry('session.use\_env\_allocators', '1')
    return ort.InferenceSession(
    model\_path,
    sess\_options=opts,
    providers=\['CPUExecutionProvider'\]
    )

## **10.4 Model Instance Pool**

A pool of N ONNX Runtime sessions is created at startup (one per CPU core group). Inference requests are dispatched to a free session from the pool. This prevents thread contention between concurrent batch inferences and maximises CPU cache reuse.

import asyncio
from contextlib import asynccontextmanager

class ONNXSessionPool:
    def\_\_init\_\_(self, model\_path: str, pool\_size: int, threads\_per\_session: int):
    self.\_sessions \= \[
    create\_ort\_session(model\_path, threads\_per\_session)
    for\_ in range(pool\_size)
    \]
    self.\_queue \= asyncio.Queue()
    for s in self.\_sessions:
    self.\_queue.put\_nowait(s)

    @asynccontextmanager
    async def acquire(self):
    session\= await self.\_queue.get()
    try:
    yield session
    finally:
    await self.\_queue.put(session)

    @classmethod
    def from\_env(cls, model\_path: str) \-\> 'ONNXSessionPool':
    \# Reads from OpenShift env vars set in DeploymentConfig
    pool\_size          \= int(os.getenv('ONNX\_POOL\_SIZE', '4'))
    threads\_per\_session= int(os.getenv('ONNX\_THREADS\_PER\_SESSION', '4'))
    return cls(model\_path, pool\_size, threads\_per\_session)

\# Startup: pool\_size=4, threads=4 on a 16-core node \-\> 16 cores fully utilised
embedding\_pool \= ONNXSessionPool.from\_env('/models/bert-multilingual-int8/model.onnx')

## **10.5 CPU Embedding Processing Loop**

**◈ REVISED — OpenShift CPU-Only:  Batch size reduced from 64 (GPU) to 16 (CPU) to stay within RAM bandwidth limits. Prefetch pipeline added.**

class EmbeddingWorker:
    def\_\_init\_\_(self):
    self.tokenizer\= BertTokenizerFast.from\_pretrained(
    'bert-base-multilingual-cased'
    )
    self.session\_pool   \= ONNXSessionPool.from\_env(MODEL\_PATH)
    self.batch\_size     \= int(os.getenv('EMBEDDING\_BATCH\_SIZE', '16'))
    self.prefetch\_queue \= asyncio.Queue(maxsize=4)  \# async prefetch

    async def run(self):
    \# Launch prefetch coroutine in parallel
    asyncio.create\_task(self.\_prefetch\_loop())
    async for batch in self.\_consume\_prefetched():
    await self.\_embed\_and\_store(batch)

    async def\_prefetch\_loop(self):
    '''Fetch chunk texts from DB while previous batch is being embedded.'''
    async for msg\_batch in self.consume\_batches(self.batch\_size):
    chunk\_ids \= \[m\['chunk\_id'\] for m in msg\_batch\]
    cached, uncached\= await embedding\_cache.get\_batch(chunk\_ids)
    if uncached:
    chunks\= await chunk\_repo.fetch\_by\_ids(uncached)
    await self.prefetch\_queue.put((msg\_batch, chunks, cached))
    else:
    await msg\_batch\_ack\_all(msg\_batch)

    async def\_embed\_and\_store(self, item):
    msg\_batch, chunks, cached \= item
    texts\= \[c.chunk\_text for c in chunks\]
    encoded\= self.tokenizer(
    texts, padding=True, truncation=True,
    max\_length=512, return\_tensors='np'  \# numpy for ONNX
    )
    async with self.session\_pool.acquire() as session:
    \# Run in threadpool to not block async event loop
    embeddings\= await asyncio.get\_event\_loop().run\_in\_executor(
    None,
    lambda: session.run(None, {
    'input\_ids':      encoded\['input\_ids'\],
    'attention\_mask': encoded\['attention\_mask'\],
    'token\_type\_ids': encoded\['token\_type\_ids'\]
    })\[0\]   \# \[batch, seq\_len, 768\] last\_hidden\_state
    )
    embeddings\= mean\_pooling\_np(embeddings, encoded\['attention\_mask'\])
    embeddings\= l2\_normalize\_np(embeddings)
    await embedding\_repo.bulk\_upsert(chunks, embeddings)
    await embedding\_cache.set\_batch(
    {c.chunk\_id: e for c, e in zip(chunks, embeddings)}
    )
    await chunk\_repo.bulk\_update\_status(\[c.chunk\_id for c in chunks\], 'done')
    for msg in msg\_batch: await msg.ack()

## **10.6 NumPy Mean Pooling (No PyTorch)**

import numpy as np

def mean\_pooling\_np(token\_embeddings: np.ndarray,
    attention\_mask:   np.ndarray) \-\> np.ndarray:
    '''Pure NumPy mean pooling — no PyTorch dependency needed at runtime.'''
    mask\_expanded \= attention\_mask\[:, :, np.newaxis\].astype(np.float32)
    sum\_embeddings \= np.sum(token\_embeddings \* mask\_expanded, axis=1)
    count\= np.clip(mask\_expanded.sum(axis=1), a\_min=1e-9, a\_max=None)
    return sum\_embeddings / count

def l2\_normalize\_np(embeddings: np.ndarray) \-\> np.ndarray:
    norms\= np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / np.clip(norms, a\_min=1e-9, a\_max=None)

## **10.7 CPU Throughput Tuning — Per-Pod Settings**

| Env Variable                | Default | Description                                                               |
| --------------------------- | ------- | ------------------------------------------------------------------------- |
| EMBEDDING\_BATCH\_SIZE      | 16      | Chunks per ONNX inference call — tuned for CPU RAM bandwidth             |
| ONNX\_POOL\_SIZE            | 4       | ONNX sessions per pod — one per core group                               |
| ONNX\_THREADS\_PER\_SESSION | 4       | intra\_op threads per session (pool\_size × threads \= total cores)      |
| OMP\_NUM\_THREADS           | 4       | OpenMP thread count; must match ONNX\_THREADS\_PER\_SESSION               |
| OMP\_WAIT\_POLICY           | PASSIVE | Avoid CPU spinning between batches                                        |
| MKL\_NUM\_THREADS           | 4       | MKL BLAS threads if Intel CPU (matches OMP)                               |
| GOMP\_SPINCOUNT             | 0       | Disable GNU OpenMP spin-waiting                                           |
| TOKENIZERS\_PARALLELISM     | false   | Disable HuggingFace tokenizer forked parallelism (conflicts with asyncio) |
| PREFETCH\_QUEUE\_SIZE       | 4       | Number of prefetched DB batches in flight                                 |

# **11\. Service 7: Retrieval API**

## **11.1 Overview**

**◈ REVISED — OpenShift CPU-Only:  Cross-encoder re-ranking uses INT8 ONNX session pool instead of PyTorch on GPU.**

The Retrieval API accepts natural language queries and executes the full hybrid retrieval pipeline on CPU. The BERT bi-encoder (for query embedding) and BERT cross-encoder (for re-ranking) both run as ONNX Runtime INT8 sessions via the same session pool pattern as the Embedding Service. Redis full-result caching is critical to meet p99 SLAs under CPU-only constraints.

## **11.2 Endpoints**

| Method | Path                               | Description                   |
| ------ | ---------------------------------- | ----------------------------- |
| POST   | /api/v1/retrieve                   | Main retrieval endpoint       |
| POST   | /api/v1/retrieve/batch             | Batch queries (up to 50\)     |
| GET    | /api/v1/retrieve/audit/{audit\_id} | Fetch past query audit record |
| GET    | /api/v1/health                     | Liveness\+ readiness probe    |

## **11.3 Request & Response Schema**

Unchanged from v1.0. The request supports k-chunk and n-document modes with full filter and config overrides. The response includes per-stage latency breakdown and all scores. See v1.0 Section 11.3–11.4 for full schemas.

# **12\. Retrieval Pipeline — Deep Dive**

**◈ REVISED — OpenShift CPU-Only:  All BERT inference steps (NER, bi-encoder query embedding, cross-encoder re-ranking) use INT8 ONNX session pool.**

## **12.1 Query BERT Inference on CPU**

Three BERT ONNX models are used in retrieval. All use the same ONNXSessionPool pattern. For the Retrieval API, the pools are sized smaller (pool\_size=2) since retrieval is latency-sensitive per-query, not throughput-sensitive like bulk embedding.

| ONNX Model           | Pool Size | Threads/Session | Use Case                  | Avg CPU Latency             |
| -------------------- | --------- | --------------- | ------------------------- | --------------------------- |
| BERT NER INT8        | 2         | 4               | Query entity extraction   | \~80 ms (query-length text) |
| BERT Bi-Enc. INT8    | 2         | 4               | Query vector embedding    | \~100 ms (single query)     |
| BERT Cross-Enc. INT8 | 2         | 4               | Re-rank top-50 candidates | \~600 ms (50 pairs)         |

## **12.2 Dense Vector Search (HNSW)**

async def dense\_search(query\_embedding, k=100, filters=None):
    filter\_clause \= build\_filter\_sql(filters)
    async with read\_replica\_pool.acquire() as conn:
    await conn.execute('SET LOCAL hnsw.ef\_search \= 200')
    rows\= await conn.fetch(f'''
    SELECT ce.chunk\_id, ce.parent\_document\_id,
    c.chunk\_text, c.page\_number, c.chunk\_index,
    c.source\_type, c.chunk\_metadata,
    1\- (ce.embedding \<=\> $1::vector) AS cosine\_score
    FROM chunk\_embeddings ce
    JOIN chunks c ON c.chunk\_id \= ce.chunk\_id
    JOIN parent\_documents pd
    ON pd.parent\_document\_id \= ce.parent\_document\_id
    WHERE pd.status\= 'ready' {filter\_clause}
    ORDER BY ce.embedding\<=\> $1::vector LIMIT $2
    ''', query\_embedding.tolist(), k)
    return\[DenseResult(\*\*dict(r)) for r in rows\]

## **12.3 Sparse BM25 Search**

Unchanged from v1.0. In-memory BM25 index refreshed every 5 minutes from PostgreSQL. At 5M chunks, \~1 GB RAM. Per-pod index, no cross-pod sharing needed.

## **12.4 RRF \+ MMR \+ Cross-Encoder**

RRF, MMR, and BERT cross-encoder logic are identical to v1.0. The only difference is the cross-encoder inference backend: ONNX Runtime INT8 session pool replaces PyTorch on GPU. Latency increases from \~200 ms (GPU) to \~600 ms (CPU INT8, 50 candidates) — acceptable within the 800 ms p99 SLA.

## **12.5 n-Document Aggregation**

Unchanged from v1.0. Walk reranked list collecting chunks until n unique parent document IDs are seen. Return primary chunk (highest score) per document with supporting chunks in response.

# **13\. Queue Architecture (RabbitMQ)**

Queue topology unchanged from v1.0. Quorum queues, DLQs, and priority routing are all preserved. On OpenShift, RabbitMQ is deployed as a StatefulSet with PersistentVolumeClaims on fast block storage (OCS/Ceph or AWS gp3).

| Exchange      | Type    | Queue                      | Routing Key  | Purpose                          |
| ------------- | ------- | -------------------------- | ------------ | -------------------------------- |
| rag.ingestion | Direct  | ingestion\_queue           | ingest.new   | New document processing tasks    |
| rag.ingestion | Direct  | ingestion\_dlq             | ingest.dead  | Failed ingestion after 3 retries |
| rag.ocr       | Direct  | ocr\_queue                 | ocr.process  | OCR image tasks                  |
| rag.ocr       | Direct  | ocr\_dlq                   | ocr.dead     | Failed OCR tasks                 |
| rag.embedding | Direct  | embedding\_queue           | embed.chunk  | Chunk embedding tasks            |
| rag.embedding | Direct  | embedding\_dlq             | embed.dead   | Failed embedding tasks           |
| rag.priority  | Headers | ingestion\_priority\_queue | priority\> 7 | High-priority documents          |

## **13.1 OpenShift RabbitMQ StatefulSet**

apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: rabbitmq
  namespace: rag-pipeline
spec:
  serviceName: rabbitmq-headless
  replicas: 3
  selector:
    matchLabels: {app: rabbitmq}
  template:
    spec:
    securityContext:
    runAsNonRoot: true
    runAsUser:    999\# rabbitmq uid
    fsGroup:      999
    containers:
    \- name: rabbitmq
    image: rabbitmq:3.13-management
    resources:
    requests: {cpu: '2', memory: 8Gi}
    limits:   {cpu: '4', memory: 16Gi}
    volumeMounts:
    \- name: data
    mountPath: /var/lib/rabbitmq
  volumeClaimTemplates:
  \- metadata: {name: data}
    spec:
    accessModes:\[ReadWriteOnce\]
    storageClassName: gp3-csi\# Fast SSD storage class
    resources:
    requests: {storage: 500Gi}

# **14\. Cache Strategy (Redis)**

Redis cache strategy unchanged from v1.0. On OpenShift, Redis runs as a StatefulSet cluster (3 primary \+ 3 replica). The embedding cache is especially critical on CPU — avoiding redundant INT8 ONNX inference saves \~900 ms per cached batch.

| Key Pattern                   | Value                  | TTL        | Purpose                           |
| ----------------------------- | ---------------------- | ---------- | --------------------------------- |
| doc:sha256:{hash}             | document\_id           | 7 days     | Ingest deduplication              |
| emb:{model\_ver}:{chunk\_id}  | float32 bytes (3072 B) | 72 hours   | Embedding cache (critical on CPU) |
| query:emb:{query\_hash}       | float32 bytes          | 1 hour     | Query embedding cache             |
| retrieval:{query+params hash} | JSON result            | 5 minutes  | Full retrieval result cache       |
| ratelimit:{api\_key}:{window} | int count              | 60 seconds | Sliding window rate limit         |
| ocr:img:{image\_sha256}       | OCR text               | 30 days    | OCR result dedup cache            |

# **15\. Storage Strategy**

Storage layout and S3 structure unchanged from v1.0. On OpenShift, an additional PVC is used for the BERT model files (Section 19 covers this in detail). PostgreSQL is deployed as a StatefulSet with ReadWriteOnce PVCs on fast block storage.

## **15.1 S3 Layout**

s3://rag-documents/
├── documents/{year}/{month}/{day}/{document\_id}/
│   ├── original.pdf
│   └── images/
├── processed/{document\_id}/
│   ├── extracted\_text.json
│   └── chunks\_manifest.json
└── models/                            ← NEW: ONNX model artifacts
    └── bert-multilingual-int8/
    ├── model.onnx                 ← INT8 quantised (\~180 MB)
    ├── tokenizer\_config.json
    ├── vocab.txt
    ├── tokenizer.json
    └── manifest.json              ← Version\+ checksum metadata

## **15.2 PGVector Index Tuning (CPU)**

* HNSW m=32, ef\_construction=200: index quality unchanged from v1.0; HNSW search itself is CPU-bound and fast regardless of GPU presence
* ef\_search=200 at query time: set via SET LOCAL per query on the read replica connection
* Read replicas: all retrieval queries hit read replicas only; primary reserved for writes
* PgBouncer: connection pooler in front of PostgreSQL; transaction-mode pooling; 200 max connections

# **16\. Security & Resilience**

Security controls from v1.0 are all preserved and augmented with OpenShift-specific hardening.

| Layer              | Control                      | Implementation                                              |
| ------------------ | ---------------------------- | ----------------------------------------------------------- |
| API Auth           | API Key (HMAC-SHA256)        | X-API-Key header; validated against Redis                   |
| Transport          | TLS 1.3                      | OpenShift Route edge TLS; Service Mesh mTLS between pods    |
| SCC                | restricted-v2                | All pods: runAsNonRoot, readOnlyRootFilesystem, no hostPath |
| Data at Rest       | AES-256                      | S3 SSE-S3; PVC on encrypted OCS/EBS volumes                 |
| Input Validation   | Pydantic v2 strict           | Field validators; reject unknown fields                     |
| File Upload Safety | MIME sniff\+ ClamAV          | Magic byte check; async antivirus scan                      |
| SQL Injection      | Parameterized queries        | asyncpg everywhere; no f-string SQL                         |
| Rate Limiting      | Redis sliding window         | 1000/min per API key; 50/min per IP                         |
| Secrets            | OpenShift Secrets\+ Vault    | DB creds, S3 keys via Vault Agent injector                  |
| Image Security     | ACS (StackRox)               | Image vulnerability scanning in CI/CD pipeline              |
| Network Policy     | OVN-Kubernetes NetworkPolicy | Namespace isolation; only allowed pod-to-pod paths          |

## **16.1 Circuit Breaker & Degradation**

* OCR Service down: text-only extraction fallback; schedule OCR retry job
* Redis down: bypass cache, query DB directly — cache is optimisation not dependency
* ONNX session pool exhausted: queue requests with async timeout; return 503 if \> 5s wait
* PostgreSQL primary unavailable: writes fail-fast; reads fall back to replica

# **17\. Observability & Monitoring**

## **17.1 Key Metrics**

| Metric                                | Type      | Alert Threshold                      |
| ------------------------------------- | --------- | ------------------------------------ |
| rag\_ingest\_documents\_total         | Counter   | Error rate\> 1% → PagerDuty         |
| rag\_queue\_depth                     | Gauge     | \> 10,000 msgs → scale workers      |
| rag\_retrieval\_latency\_ms           | Histogram | p99\> 800 ms → alert                |
| rag\_onnx\_inference\_duration\_ms    | Histogram | p99\> 1500 ms → alert; pool starved |
| rag\_onnx\_pool\_wait\_ms             | Histogram | p99\> 200 ms → increase pool\_size  |
| rag\_embedding\_batch\_duration\_ms   | Histogram | p99\> 2000 ms (CPU) → scale pods    |
| rag\_cache\_hit\_ratio                | Gauge     | \< 0.65 → investigate               |
| rag\_pgvector\_search\_ms             | Histogram | p99\> 100 ms → tune ef\_search      |
| rag\_ocp\_pod\_cpu\_throttling\_ratio | Gauge     | \> 0.1 → increase CPU limit         |

## **17.2 OpenShift-Specific Observability**

* OpenShift Monitoring Stack (Prometheus Operator \+ Alertmanager) pre-installed; custom ServiceMonitor CRs added for each RAG service
* OpenShift Logging (Elasticsearch \+ Kibana via Cluster Logging Operator) collects structlog JSON from all pods
* Distributed tracing via OpenTelemetry Operator → Jaeger Operator deployed in rag-pipeline namespace
* OpenShift Console dashboards: custom Grafana dashboards via Grafana Operator

# **18\. OpenShift Deployment — Full Specification**

**◈ REVISED — OpenShift CPU-Only:  Full section replacement from v1.0. Kubernetes → OpenShift. GPU tolerations removed. SCCs added. Routes replace Ingress.**

## **18.1 Namespace & Project Structure**

\# Create OpenShift project (namespace)
oc new-project rag-pipeline \\
  \--display-name='Enterprise RAG Pipeline' \\
  \--description='Ingestion, Chunking, Embedding, Retrieval'

\# Apply resource quotas at project level
oc apply \-f \- \<\<EOF
apiVersion: v1
kind: ResourceQuota
metadata:
  name: rag-pipeline-quota
  namespace: rag-pipeline
spec:
  hard:
    requests.cpu:     '400'\# 400 cores total project budget
    requests.memory:  800Gi
    limits.cpu:       '600'
    limits.memory:    1200Gi
    pods:             '300'
    persistentvolumeclaims: '50'
EOF

## **18.2 Security Context Constraints**

All RAG pipeline pods run under the OpenShift restricted-v2 SCC — the most restrictive built-in SCC. No custom SCC is required. This means: no privileged containers, no hostPath volumes, non-root UID enforced, read-only root filesystem with explicit emptyDir mounts for writeable paths.

\# Verify SCC is restricted-v2 for service accounts
oc get scc restricted-v2 \-o yaml | grep \-E 'allowPrivilege|runAsUser|readOnly'

\# All pod specs include:
securityContext:
  runAsNonRoot:             true
  runAsUser:                1001   \# Fixed non-root UID baked into Dockerfile
  readOnlyRootFilesystem:   true
  allowPrivilegeEscalation: false
  seccompProfile:
    type: RuntimeDefault
  capabilities:
    drop:\[ALL\]

\# Writeable paths via emptyDir only:
volumes:
  \- name: tmp        \# /tmp for all services
    emptyDir: {}
  \- name: model-cache \# /models — populated by init container
    emptyDir:
    sizeLimit: 2Gi\# INT8 ONNX model \~180 MB \+ tokenizer files

## **18.3 Container Specifications (CPU-Only)**

| Service           | Base Image                 | CPU Req/Limit | Mem Req/Limit | GPU  |
| ----------------- | -------------------------- | ------------- | ------------- | ---- |
| ingest-api        | python:3.12-slim           | 0.5 / 2       | 512 MB / 1 GB | None |
| ingestion-worker  | python:3.12-slim           | 4 / 6         | 3 GB / 5 GB   | None |
| ocr-service       | python:3.12-slim+tesseract | 2 / 4         | 1 GB / 2 GB   | None |
| embedding-service | python:3.12-slim           | 16 / 20       | 6 GB / 10 GB  | None |
| retrieval-api     | python:3.12-slim           | 8 / 12        | 4 GB / 8 GB   | None |
| rabbitmq          | rabbitmq:3.13-management   | 2 / 4         | 8 GB / 16 GB  | None |
| redis             | redis:7.2-alpine           | 1 / 2         | 16 GB / 32 GB | None |
| postgresql        | pgvector/pgvector:pg16     | 8 / 16        | 32 GB / 64 GB | None |

*⚠  The embedding-service requests 16 CPU to support 4 ONNX sessions × 4 threads each. This is intentionally high. OpenShift will schedule these pods only on nodes with sufficient CPU headroom. Do NOT reduce below 12 CPU request without also reducing ONNX\_POOL\_SIZE and ONNX\_THREADS\_PER\_SESSION.*

## **18.4 OpenShift Routes**

\# Ingest API Route — TLS edge termination at OpenShift router
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: ingest-api-route
  namespace: rag-pipeline
  annotations:
    haproxy.router.openshift.io/timeout:          120s
    haproxy.router.openshift.io/balance:          roundrobin
    haproxy.router.openshift.io/disable\_cookies:  'true'
spec:
  host: ingest.rag.internal.company.com
  to:
    kind: Service
    name: ingest-api-svc
    weight: 100
  port:
    targetPort: 8000
  tls:
    termination:                 edge
    insecureEdgeTerminationPolicy: Redirect
    \# Certificate managed by cert-manager \+ Let's Encrypt
    \# or injected from cluster cert store

## **18.5 HorizontalPodAutoscaler — Embedding Service**

\# Scale on RabbitMQ queue depth (custom metric via kube-state-metrics adapter)
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: embedding-service-hpa
  namespace: rag-pipeline
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind:       Deployment
    name:       embedding-service
  minReplicas: 40
  maxReplicas: 80
  metrics:
  \- type: External
    external:
    metric:
    name: rabbitmq\_queue\_messages\_ready
    selector:
    matchLabels: {queue: embedding\_queue}
    target:
    type:         AverageValue
    averageValue: '200'\# Target: 200 messages per replica
  \- type: Resource
    resource:
    name: cpu
    target:
    type:               Utilization
    averageUtilization: 75\# Also scale on CPU %
  behavior:
    scaleUp:
    stabilizationWindowSeconds: 30
    policies:
    \- type: Pods
    value: 8\# Add up to 8 pods per 60s
    periodSeconds: 60
    scaleDown:
    stabilizationWindowSeconds: 600\# 10-min cool-down to prevent thrash

## **18.6 Liveness & Readiness Probes**

\# Embedding Service — readiness waits for ONNX pool to warm up
livenessProbe:
  httpGet: {path: /health/live, port: 8080}
  initialDelaySeconds: 60    \# Model load \+ pool init takes \~45s on CPU
  periodSeconds:       15
  failureThreshold:    3
readinessProbe:
  httpGet: {path: /health/ready, port: 8080}
  initialDelaySeconds: 90    \# Ensure ONNX pool fully warmed before traffic
  periodSeconds:       10
  failureThreshold:    2
startupProbe:
  httpGet: {path: /health/started, port: 8080}
  failureThreshold:    30
  periodSeconds:       10    \# 300s total startup budget for model download

## **18.7 OpenShift CI/CD Pipeline (Tekton)**

\# Simplified pipeline stages
Pipeline: rag-build-deploy
  Task 1: git-clone          — Clone source from GitLab
  Task 2: unit-test          — pytest with coverage gate (\>85%)
  Task 3: build-image        — buildah build (no Docker daemon needed)
  Task 4: image-scan         — ACS/StackRox vulnerability scan; fail on CRITICAL
  Task 5: push-to-registry   — Push to OpenShift Internal Registry
  Task 6: quantise-model     — Run ONNX INT8 quantisation job (if model changed)
  Task 7: upload-model       — Push ONNX artifacts to S3 models/
  Task 8: deploy-staging     — oc apply \-k overlays/staging
  Task 9: smoke-test         — Run retrieval API smoke tests against staging
  Task 10: promote-prod      — oc tag image:staging image:prod; oc rollout

# **19\. BERT Model Loading in OpenShift Production Pods**

*✔  This is a new section introduced in Revision 2.0. It covers the complete end-to-end strategy for distributing, loading, caching, and managing INT8 ONNX BERT model files in OpenShift production pods — covering all constraints of an OpenShift CPU-only environment.*

## **19.1 The Core Challenge**

BERT INT8 ONNX models are binary artifacts (\~180 MB for the embedding model \+ \~180 MB for the cross-encoder \+ tokenizer files). They must be available inside every pod that performs inference: embedding-service (40–80 replicas) and retrieval-api (5–20 replicas). Baking large model binaries into container images is wasteful (slow pulls, excessive registry storage) and makes model version upgrades require full image rebuilds. The chosen strategy is: build slim application images, download models at pod startup via an Init Container from S3, and cache them on a shared emptyDir volume.

## **19.2 Design Constraints (OpenShift-Specific)**

* No GPU or special hardware — all model loading must work on standard CPU nodes
* restricted-v2 SCC: no hostPath volumes; model files cannot be pre-placed on nodes
* ReadOnlyRootFilesystem: model files must land in explicitly declared volumes
* Image pull policy: IfNotPresent — slim images pull fast; model download happens in init container
* Pod startup time budget: 300 seconds (startupProbe failureThreshold=30 × period=10s)
* Horizontal scaling: 80 pods may start simultaneously; S3 must handle burst download load
* Model versioning: model version is controlled by an env var; no image rebuild on model update

## **19.3 Model Distribution Architecture**

Three components work together: (1) a model-init Init Container that downloads the model from S3, (2) a shared emptyDir volume that is mounted by both the init container and the main application container, and (3) a model version ConfigMap that controls which model version is active without requiring a Deployment rollout.

    ┌─────────────────────────────────────────────────┐
    │  OpenShift Pod: embedding-service               │
  ┌──────────┐   │  ┌─────────────────┐   ┌─────────────────────┐ │
  │  S3      │   │  │ Init Container   │   │ App Container       │ │
  │  models/ │◄──┼──│ model-downloader │   │ embedding-service   │ │
  │  int8/   │   │  │                 │   │                     │ │
  └──────────┘   │  │ 1\. Read version  │   │ 1\. Wait for /models │ │
    │  │    from ConfigMap│   │    populated        │ │
  ┌──────────┐   │  │ 2\. Check SHA256  │   │ 2\. Load ONNX session│ │
  │ConfigMap │   │  │    vs Redis      │   │    pool from volume │ │
  │model-ver │──►│  │ 3\. Download from │   │ 3\. Signal readiness │ │
  └──────────┘   │  │    S3 if needed  │   │    probe /ready     │ │
    │  │ 4\. Verify SHA256 │   │                     │ │
  ┌──────────┐   │  │ 5\. Write to vol  │   └─────────────────────┘ │
  │ Redis    │   │  └────────┬────────┘             │               │
  │ model    │◄──┼──── SHA check ──────  emptyDir: /models ─────────┘
  │ sha cache│   │
  └──────────┘   └─────────────────────────────────────────────────┘

## **19.4 Init Container — model-downloader**

\# Pod spec: init container \+ volume declaration
initContainers:
\- name: model-downloader
  image: rag-pipeline/model-downloader:latest   \# Slim Python \+ boto3 image
  securityContext:
    runAsNonRoot:             true
    runAsUser:                1001
    readOnlyRootFilesystem:   true
    allowPrivilegeEscalation: false
    capabilities: {drop:\[ALL\]}
  env:
  \- name: MODEL\_VERSION
    valueFrom:
    configMapKeyRef:
    name: rag-model-config
    key:  bert\_embedding\_version  \# e.g. 'v3-int8-2024-11'
  \- name: S3\_BUCKET
    value: rag-documents
  \- name: S3\_PREFIX
    value: models/bert-multilingual-int8
  \- name: MODEL\_DEST
    value: /models
  \- name: AWS\_ACCESS\_KEY\_ID
    valueFrom: {secretKeyRef: {name: s3-creds, key: access\_key}}
  \- name: AWS\_SECRET\_ACCESS\_KEY
    valueFrom: {secretKeyRef: {name: s3-creds, key: secret\_key}}
  \- name: REDIS\_URL
    valueFrom: {secretKeyRef: {name: redis-creds, key: url}}
  volumeMounts:
  \- name: model-vol
    mountPath: /models
  \- name: tmp-vol
    mountPath: /tmp
  resources:
    requests: {cpu: '0.5', memory: 512Mi}
    limits:   {cpu: '2',   memory: 1Gi}

volumes:
\- name: model-vol
  emptyDir:
    sizeLimit: 2Gi\# INT8 BERT \~180 MB x 2 models \+ tokenizer files
\- name: tmp-vol
  emptyDir: {}

## **19.5 Init Container — Downloader Script**

\#\!/usr/bin/env python3
\# model\_downloader.py — runs inside init container
import os, hashlib, json, asyncio, sys
import boto3, redis
from pathlib import Path

MODEL\_VERSION \= os.environ\['MODEL\_VERSION'\]
S3\_BUCKET     \= os.environ\['S3\_BUCKET'\]
S3\_PREFIX     \= os.environ\['S3\_PREFIX'\]
MODEL\_DEST    \= Path(os.environ\['MODEL\_DEST'\])
REDIS\_URL     \= os.environ\['REDIS\_URL'\]

s3  \= boto3.client('s3')
rdb \= redis.from\_url(REDIS\_URL, decode\_responses=True)

def get\_model\_manifest() \-\> dict:
    '''Download manifest.json — lists all model files\+ expected SHA256 hashes.'''
    key\= f'{S3\_PREFIX}/{MODEL\_VERSION}/manifest.json'
    obj\= s3.get\_object(Bucket=S3\_BUCKET, Key=key)
    return json.loads(obj\['Body'\].read())

def sha256\_file(path: Path) \-\> str:
    h\= hashlib.sha256()
    with open(path, 'rb') as f:
    for chunk in iter(lambda: f.read(65536), b''):
    h.update(chunk)
    return h.hexdigest()

def is\_model\_current(manifest: dict) \-\> bool:
    '''Check Redis cache: has this pod already downloaded the correct version?'''
    cache\_key \= f'model:downloaded:{MODEL\_VERSION}'
    return rdb.get(cache\_key) \== 'ok'

def download\_model(manifest: dict):
    for file\_entry in manifest\['files'\]:
    rel\_path   \= file\_entry\['path'\]           \# e.g. 'model.onnx'
    s3\_key     \= f'{S3\_PREFIX}/{MODEL\_VERSION}/{rel\_path}'
    dest\_path  \= MODEL\_DEST / rel\_path
    expected\= file\_entry\['sha256'\]
    dest\_path.parent.mkdir(parents=True, exist\_ok=True)
    \# Skip if already downloaded with correct hash
    if dest\_path.exists() and sha256\_file(dest\_path) \== expected:
    print(f'\[SKIP\] {rel\_path} already correct')
    continue
    print(f'\[DOWNLOAD\] {s3\_key} \-\> {dest\_path}')
    s3.download\_file(S3\_BUCKET, s3\_key, str(dest\_path))
    actual\= sha256\_file(dest\_path)
    if actual\!= expected:
    print(f'\[ERROR\] SHA256 mismatch: {rel\_path}', file=sys.stderr)
    sys.exit(1)
    print(f'\[OK\] {rel\_path} ({file\_entry\["size\_mb"\]:.1f} MB)')

def main():
    print(f'Model version: {MODEL\_VERSION}')
    manifest\= get\_model\_manifest()
    if not is\_model\_current(manifest):
    download\_model(manifest)
    else:
    print('\[CACHED\] Model files up-to-date')
    \# Write version file so main container can verify
    (MODEL\_DEST / '.version').write\_text(MODEL\_VERSION)
    print('\[DONE\] Model ready at', MODEL\_DEST)

if \_\_name\_\_ \== '\_\_main\_\_':
    main()

## **19.6 manifest.json Format**

\# s3://rag-documents/models/bert-multilingual-int8/v3-int8-2024-11/manifest.json
{
    'version':     'v3-int8-2024-11',
    'model\_type':  'bert-multilingual-int8',
    'onnx\_opset':  17,
    'created\_at':  '2024-11-01T10:00:00Z',
    'total\_size\_mb': 312,
    'files':\[
    {
    'path':     'model.onnx',
    'sha256':   'a1b2c3d4...',
    'size\_mb':  178.4
    },
    {
    'path':     'tokenizer.json',
    'sha256':   'e5f6g7h8...',
    'size\_mb':  0.8
    },
    {
    'path':     'vocab.txt',
    'sha256':   'i9j0k1l2...',
    'size\_mb':  0.9
    },
    {
    'path':     'tokenizer\_config.json',
    'sha256':   'm3n4o5p6...',
    'size\_mb':  0.01
    }
    \]
}

## **19.7 Application Container — Model Startup Sequence**

The main application container does not start inference until it has verified that the model volume was populated correctly by the init container. The readiness probe gates traffic until this is complete.

\# embedding\_service/startup.py
import onnxruntime as ort
from pathlib import Path
import os, sys, logging

MODEL\_PATH     \= Path(os.getenv('MODEL\_DEST', '/models'))
MODEL\_VERSION  \= os.getenv('MODEL\_VERSION')
ONNX\_POOL\_SIZE \= int(os.getenv('ONNX\_POOL\_SIZE', '4'))
ONNX\_THREADS   \= int(os.getenv('ONNX\_THREADS\_PER\_SESSION', '4'))

def verify\_model\_integrity() \-\> str:
    '''Verify .version file matches expected MODEL\_VERSION.'''
    version\_file \= MODEL\_PATH / '.version'
    if not version\_file.exists():
    raise RuntimeError('Model not ready: .version file missing')
    actual\= version\_file.read\_text().strip()
    if actual\!= MODEL\_VERSION:
    raise RuntimeError(f'Model version mismatch: got {actual}, want {MODEL\_VERSION}')
    onnx\_file \= MODEL\_PATH / 'model.onnx'
    if not onnx\_file.exists():
    raise RuntimeError('model.onnx not found')
    logging.info(f'Model integrity OK: version={actual}, size={onnx\_file.stat().st\_size}')
    return str(onnx\_file)

def warm\_up\_onnx\_pool(session\_pool: ONNXSessionPool):
    '''
    Run a dummy inference pass on each session to trigger JIT compilation,
    kernel initialisation, and memory arena allocation.
    Without warmup, first real request suffers 3-5x latency spike.
    '''
    import numpy as np
    dummy\_ids   \= np.zeros((1, 128), dtype=np.int64)
    dummy\_mask  \= np.ones( (1, 128), dtype=np.int64)
    dummy\_types \= np.zeros((1, 128), dtype=np.int64)
    for session in session\_pool.\_sessions:
    session.run(None, {
    'input\_ids':      dummy\_ids,
    'attention\_mask': dummy\_mask,
    'token\_type\_ids': dummy\_types
    })
    logging.info(f'ONNX pool warmed up: {ONNX\_POOL\_SIZE} sessions ready')

\# FastAPI lifespan
from contextlib import asynccontextmanager
@asynccontextmanager
async def lifespan(app: FastAPI):
    onnx\_path   \= verify\_model\_integrity()
    session\_pool \= ONNXSessionPool(onnx\_path, ONNX\_POOL\_SIZE, ONNX\_THREADS)
    warm\_up\_onnx\_pool(session\_pool)
    app.state.session\_pool \= session\_pool
    app.state.tokenizer\= BertTokenizerFast.from\_pretrained(str(MODEL\_PATH))
    logging.info('Embedding service ready')
    yield
    \# Shutdown: sessions closed automatically by ORT GC

## **19.8 Model Version Upgrade Procedure (Zero-Downtime)**

Upgrading the BERT model version does not require rebuilding any container image. The procedure is a ConfigMap update followed by a rolling pod restart, which is safe because the HPA ensures minimum replicas remain available.

| Step | Action                              | Command / Detail                                                                                 |
| ---- | ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| 1    | Upload new ONNX model to S3         | aws s3 cp models/ s3://rag-documents/models/bert-multilingual-int8/v4-int8-2025-01/\--recursive  |
| 2    | Verify SHA256 checksums in manifest | python scripts/generate\_manifest.py v4-int8-2025-01 → upload manifest.json                     |
| 3    | Update ConfigMap                    | oc patch configmap rag-model-config\-p '{"data":{"bert\_embedding\_version":"v4-int8-2025-01"}}' |
| 4    | Trigger rolling restart             | oc rollout restart deployment/embedding-service                                                  |
| 5    | Monitor rollout                     | oc rollout status deployment/embedding-service\--watch                                           |
| 6    | Validate inference quality          | Run retrieval smoke test suite against staging; compare MRR scores                               |
| 7    | Repeat for retrieval-api            | oc rollout restart deployment/retrieval-api                                                      |
| 8    | Rollback if needed                  | oc rollout undo deployment/embedding-service → auto-reverts ConfigMap binding                   |

*ℹ  The rolling restart strategy means old model version pods and new model version pods coexist briefly. This is safe because the query embedding and chunk embedding use the same model version — both are immutable once written to PGVector. The only risk is a temporary score distribution shift during the rollout window (\~5 minutes at 80 pods), which RRF and cross-encoder re-ranking absorb gracefully.*

## **19.9 Model File Performance on emptyDir vs PVC**

| Storage Option      | First Load    | Subsequent Loads    | Shared Across Pods | Recommended                             |
| ------------------- | ------------- | ------------------- | ------------------ | --------------------------------------- |
| emptyDir (tmpfs)    | \~45s (S3 dl) | \~45s (re-download) | No — per pod      | YES — simplest, SCC-compliant          |
| emptyDir (disk)     | \~45s (S3 dl) | \~45s (re-download) | No — per pod      | Fallback if tmpfs RAM too costly        |
| PVC (ReadWriteMany) | \~45s (S3 dl) | \~2s (PVC read)     | YES — all pods    | Consider for 80+ pod clusters           |
| Image baked in      | \~60s (pull)  | \~5s (IfNotPresent) | YES — per image   | Not recommended — model+image coupling |
| ConfigMap           | N/A           | N/A                 | YES                | Not suitable — 1 MB ConfigMap limit    |

*ℹ  emptyDir is the default recommendation. It is fully SCC-compliant, requires no StorageClass, and the \~45s download is covered by the 300s startupProbe budget. For clusters with 80+ simultaneous pod starts, a ReadWriteMany PVC (OCS CephFS) or an S3 presigned URL CDN (CloudFront) reduces S3 download burst load.*

## **19.10 Multi-Model Management (NER \+ Embedding \+ Cross-Encoder)**

The retrieval-api pod requires three ONNX models: NER, bi-encoder embedding, and cross-encoder. The init container handles all three using the same manifest pattern but separate S3 prefixes and ConfigMap keys.

\# rag-model-config ConfigMap — controls all model versions
apiVersion: v1
kind: ConfigMap
metadata:
  name: rag-model-config
  namespace: rag-pipeline
data:
  bert\_embedding\_version:     'v3-int8-2024-11'
  bert\_cross\_encoder\_version: 'v2-int8-2024-09'
  bert\_ner\_version:           'v1-int8-2024-06'
  onnx\_pool\_size:             '4'
  onnx\_threads\_per\_session:   '4'
  embedding\_batch\_size:       '16'

\# Retrieval API pod: three init containers run sequentially
initContainers:
\- name: download-embedding-model
  env:
  \- name: MODEL\_VERSION
    valueFrom: {configMapKeyRef: {name: rag-model-config,
    key: bert\_embedding\_version}}
  \- name: S3\_PREFIX
    value: models/bert-multilingual-int8
  \- name: MODEL\_DEST
    value: /models/embedding
\- name: download-crossencoder-model
  env:
  \- name: MODEL\_VERSION
    valueFrom: {configMapKeyRef: {name: rag-model-config,
    key: bert\_cross\_encoder\_version}}
  \- name: S3\_PREFIX
    value: models/bert-cross-encoder-int8
  \- name: MODEL\_DEST
    value: /models/crossencoder
\- name: download-ner-model
  env:
  \- name: MODEL\_VERSION
    valueFrom: {configMapKeyRef: {name: rag-model-config,
    key: bert\_ner\_version}}
  \- name: S3\_PREFIX
    value: models/bert-ner-int8
  \- name: MODEL\_DEST
    value: /models/ner

## **19.11 ONNX Runtime Memory Layout per Pod**

| Component                       | Memory Usage | Notes                                                  |
| ------------------------------- | ------------ | ------------------------------------------------------ |
| INT8 BERT model weights         | \~180 MB     | Loaded into ORT memory arena at session creation       |
| ORT memory arena (×4 sessions) | \~400 MB     | Per-session arena; amortised across concurrent batches |
| Tokenizer\+ vocab               | \~50 MB      | Loaded once, shared across sessions via Python object  |
| Input/output tensors (batch=16) | \~50 MB      | float32\[16, 512, 768\] ≈ 25 MB in \+ 25 MB out       |
| BM25 index (in retrieval-api)   | \~1 GB       | 5M chunks × 200 B average; refreshed every 5 min      |
| Total per embedding-service pod | \~6 GB       | Fits in 6 GB request / 10 GB limit                     |
| Total per retrieval-api pod     | \~4 GB       | 3 models\+ BM25 \+ embeddings cache                    |

# **20\. Performance Benchmarks & SLAs (CPU-Only)**

**◈ REVISED — OpenShift CPU-Only:  All GPU latency figures removed. CPU INT8 ONNX benchmarks applied. Pod counts scaled up to compensate.**

## **20.1 Target SLAs — CPU-Only Baseline**

| Operation                                  | p50           | p99            | Max Error Rate |
| ------------------------------------------ | ------------- | -------------- | -------------- |
| POST /ingest (API ack)                     | \< 200 ms     | \< 500 ms      | \< 0.1%        |
| Document fully embedded (end-to-end)       | \< 90 seconds | \< 300 seconds | \< 0.5%        |
| OCR page processing (Tesseract)            | \< 5 seconds  | \< 20 seconds  | \< 1%          |
| BERT embedding batch (16 chunks, CPU INT8) | \< 900 ms     | \< 2000 ms     | \< 0.1%        |
| Dense search (HNSW, 5M vectors)            | \< 20 ms      | \< 50 ms       | \< 0.01%       |
| Sparse search (BM25, 5M chunks)            | \< 5 ms       | \< 20 ms       | \< 0.01%       |
| BERT NER preprocessing (CPU INT8)          | \< 100 ms     | \< 200 ms      | \< 0.1%        |
| Query BERT embedding (CPU INT8)            | \< 120 ms     | \< 250 ms      | \< 0.1%        |
| Cross-encoder re-rank (50 cand, CPU INT8)  | \< 600 ms     | \< 1200 ms     | \< 0.1%        |
| Full retrieval API response                | \< 400 ms     | \< 1000 ms     | \< 0.1%        |
| Ingestion throughput                       | 100,000/hr    | —             | —             |
| Retrieval QPS                              | 3,000 QPS     | —             | —             |

*⚠  Retrieval QPS drops from 10,000 (GPU) to 3,000 (CPU) because the cross-encoder re-ranking is the bottleneck at \~600 ms per query. Horizontal scaling of retrieval-api pods (up to 20\) and aggressive Redis caching of repeated queries (cache-hit rate target: 70%+) compensates for the throughput reduction.*

## **20.2 CPU Throughput Math — 100,000 Documents/Hour**

Target: 100,000 docs/hr \= 27.8 docs/second

Assumptions (same as v1.0):
  Avg doc size:     10 MB (mixed text \+ images)
  Avg pages/doc:    25 pages
  Avg chunks/doc:   50 chunks (512-token, recursive split)
  Avg OCR pages:    8 per doc (30% image)

─── Ingestion Worker (CPU, 6 coroutines/pod) ──────────────────────
  S3 download:       \~2.0s
  Text extraction:   \~0.5s  (PyMuPDF — CPU, fast)
  OCR dispatch:      \~0.1s  (async non-blocking)
  Chunking:          \~0.1s  (tokenizer CPU, fast)
  DB bulk insert:    \~0.2s  (asyncpg COPY)
  Queue publish:     \~0.05s
  Total per doc:     \~3.0s  → 6 coroutines → 2.0 docs/s/pod
  Pods needed:       27.8 / 2.0 \= 14 → deploy 25 with headroom

─── Embedding Service (CPU INT8, batch=16) ────────────────────────
  DB fetch 16 chunks:         \~30ms
  ONNX INT8 inference:        \~900ms  (16 chunks @ 512 tokens, CPU)
  PGVector bulk write:        \~50ms
  Total per batch:            \~980ms → 61 batches/min/pod
  Chunks/min per pod:         61 × 16 \= 976 chunks/min
  Docs/hr per pod:            (976/50) × 60 \= 1,171 docs/hr
  Pods needed:                100,000 / 1,171 \= 85 pods
  Deploy:                     40 baseline → autoscale to 80 → queue absorbs burst
  Strategy:                   HPA triggers scaleup when queue \> 200 msgs/replica

─── Key insight: embedding is the CPU bottleneck ──────────────────
  Embedding: 80 pods × 1,171 docs/hr \= 93,680 docs/hr (at max scale)
  Gap:       6,320 docs/hr handled by queue buffer (absorbs \~20 min of lag)
  Resolution: ingest bursts are absorbed by RabbitMQ depth; steady-state OK
  Alternative: reduce chunk\_size to 256 tokens → halves inference time

## **20.3 Minimum OpenShift Infrastructure for 100K docs/hr**

| Component           | Min Pods/Nodes   | Spec per Pod/Node                                  |
| ------------------- | ---------------- | -------------------------------------------------- |
| ingest-api          | 3 pods           | 0.5 CPU req / 2 CPU limit / 512 MB RAM             |
| ingestion-worker    | 25 pods          | 4 CPU / 6 CPU limit / 3 GB RAM                     |
| ocr-service         | 10 pods          | 2 CPU / 4 CPU limit / 1 GB RAM                     |
| embedding-service   | 40–80 pods      | 16 CPU req / 20 CPU limit / 6 GB RAM — HPA driven |
| retrieval-api       | 5–20 pods       | 8 CPU req / 12 CPU limit / 4 GB RAM                |
| rabbitmq            | 3 pods (quorum)  | 2 CPU / 4 CPU limit / 8 GB RAM / 500 GB PVC        |
| redis               | 6 pods (cluster) | 1 CPU / 2 CPU limit / 16 GB RAM                    |
| postgresql primary  | 1 pod            | 8 CPU / 16 CPU limit / 32 GB RAM / 4 TB PVC        |
| postgresql replicas | 2 pods           | 8 CPU / 16 CPU limit / 32 GB RAM / 4 TB PVC        |
| OpenShift CPU nodes | \~20 nodes       | 64 CPU / 256 GB RAM each (adjust per cloud)        |

# **21\. Future Extensibility**

## **21.1 GPU Readiness (When Available)**

The ONNX Runtime backend is already the inference abstraction. When GPU nodes become available in the OpenShift cluster, the only change required is: (1) add 'CUDAExecutionProvider' to the ONNX session providers list, (2) change the batch size from 16 to 64, and (3) remove the ONNX\_POOL\_SIZE scaling (single session saturates GPU). No application logic changes. The model files themselves (INT8 ONNX) are compatible with both CPU and CUDA providers.

\# GPU-ready session creation (one env var change)
providers \= \['CUDAExecutionProvider', 'CPUExecutionProvider'\]  \# GPU mode
\# vs
providers \= \['CPUExecutionProvider'\]  \# Current CPU-only mode

\# Controlled by INFERENCE\_PROVIDER env var in ConfigMap
provider \= os.getenv('INFERENCE\_PROVIDER', 'CPUExecutionProvider')

## **21.2 Pluggable Chunking Strategies**

Strategy-pattern registry unchanged from v1.0. New strategies registered as Python entry\_points plugins. No pipeline changes required.

## **21.3 Multi-Tenant Support**

Add tenant\_id to parent\_documents and chunk\_embeddings. Partition tables by tenant. OpenShift Namespaces provide natural tenant isolation at the infrastructure level — one namespace per tenant with dedicated quotas.

## **21.4 Advanced Retrieval (Roadmap)**

* HyDE: Hypothetical document embedding using BERT MLM on CPU — compatible with INT8 ONNX
* Query Decomposition: sub-query splitting using BERT QA (CPU INT8 ONNX)
* Streaming SSE: Server-Sent Events for progressive result delivery
* Relevance feedback: log interaction signals → fine-tune cross-encoder INT8 model quarterly
* Elasticsearch sparse backend: drop-in replacement behind SparseRetriever interface for 50M+ chunk scale

## **21.5 OpenShift Operator (Long-Term)**

A custom OpenShift Operator (built with Operator SDK) can manage the full RAG pipeline lifecycle: CRD-driven deployment, automatic model version upgrades, queue depth monitoring with custom HPA triggers, and one-command scaling. This removes manual kubectl/oc operations for the infrastructure team.

**END OF DESIGN DOCUMENT — REVISION 2.0**
OpenShift CPU-Only Revision  ·  Supersedes v1.0 (GPU/Kubernetes)  ·  For Architecture Review
