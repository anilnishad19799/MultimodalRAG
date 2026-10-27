# Multimodal RAG

A local-first, Docker Compose application for uploading PDFs and images, searching their text and visual content, and asking questions in a persistent chat. A LangGraph router selects either a general-answer agent or a document-grounded RAG agent. The browser UI streams chat output and ingestion progress.

## Features

- **PDF and image ingestion:** accepts PDF, PNG, JPEG, and WebP. PDF extraction uses Unstructured's high-resolution partitioner for layout, text, tables, and OCR, and PyMuPDF renders pages to retain charts and diagrams.
- **Multimodal retrieval:** stores extracted text and table representations, image descriptions, and source metadata. At answer time, retrieved original images can be sent to the vision/chat model and displayed with citations.
- **Hybrid search:** combines OpenAI embeddings in Qdrant with PostgreSQL full-text search. The model must cite retrieved evidence for document-grounded answers; citations are checked against the retrieved evidence and repeated citations to the same source page are collapsed.
- **General and document agents:** a LangGraph LLM router sends general questions to the general-answer agent and questions requiring uploaded-source evidence to the document RAG agent.
- **Conversation memory:** maintains recent turns and an LLM-generated thread summary. LLM-extracted semantic, episodic, and procedural memories are stored in PostgreSQL and searched in a dedicated Qdrant memory collection across threads.
- **User accounts:** email and password sign-up/sign-in, expiring HttpOnly signed sessions, and user-owned threads, memories, uploads, and document search.
- **Live UI updates:** PDF ingestion progress uses Server-Sent Events (SSE), including stage, percentage, and available page/text/table/image counts. Chat responses stream as the model generates them.
- **Local services:** PostgreSQL stores records and metadata, Qdrant stores vectors, Redis carries Celery jobs and progress events, and RustFS stores original PDF and image objects through an S3-compatible API.

## Requirements

- Docker Desktop with Docker Compose v2, or Docker Engine and the Compose plugin.
- An OpenAI API key for embeddings.
- An API key for the selected chat and vision provider: OpenAI, Groq, or Gemini.
- Internet access during the first image build and first PDF processing. The image build downloads CPU-only PyTorch dependencies; PDF layout processing can download model assets at runtime.

Allow several gigabytes of disk space for Docker images, model cache, uploaded files, and database/vector data. High-resolution PDF parsing can use substantial CPU and memory, especially for long or scanned PDFs.

## Quick start

From the repository root:

1. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. Set `SESSION_SECRET` to a unique random value (at least 32 characters), `OPENAI_API_KEY`, and the key for `MODEL_PROVIDER` (`OPENAI_API_KEY`, `GROQ_API_KEY`, or `GEMINI_API_KEY`). OpenAI embeddings are used regardless of the selected chat/vision provider, so `OPENAI_API_KEY` is always required.

3. Build and start the services:

   ```bash
   docker compose up --build -d
   ```

   The API applies Alembic migrations at startup. The first build may take several minutes because it installs CPU-only PyTorch and application dependencies.

4. Check that the services are running and the API is ready:

   ```bash
   docker compose ps
   curl http://localhost:8000/api/v1/health
   ```

5. Open the application at <http://localhost:8000>. The API reference is at <http://localhost:8000/docs>.

Create an account from the sign-in screen. For an HTTPS production deployment, set `APP_ENV=production` and `SESSION_COOKIE_SECURE=true`; the app refuses production startup with a missing/weak session secret or non-secure session cookies.

Useful local service pages:

- Qdrant: <http://localhost:6333/dashboard>
- RustFS development console: <http://localhost:9001>

The API and these host ports bind to loopback for local use. PostgreSQL and Redis are only exposed on the Compose network.

## Configure `.env`

The starting values are in [.env.example](.env.example). Change secrets before using the stack on a shared machine.

| Variable | Purpose | Default/example |
| --- | --- | --- |
| `MODEL_PROVIDER` | Chat and vision provider: `openai`, `groq`, or `gemini` | `openai` |
| `SESSION_SECRET` | Signs browser sessions; use a unique random secret of at least 32 characters | Required by Compose |
| `SESSION_COOKIE_SECURE` | Restricts session cookies to HTTPS | `false` for local HTTP; `true` for HTTPS |
| `OPENAI_API_KEY` | OpenAI embeddings key; also the chat key when provider is OpenAI | Empty; required |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | Chat/vision key when the corresponding provider is selected | Empty |
| `OPENAI_MODEL`, `GROQ_MODEL`, `GEMINI_MODEL` | Chat model name for the selected provider | See `.env.example` |
| `EMBEDDING_MODEL` | OpenAI embedding model | `text-embedding-3-large` |
| `EMBEDDING_DIMENSION` | Vector length created by the embedding model | `3072` |
| `DATABASE_URL` | SQLAlchemy/PostgreSQL connection | Compose `postgres` service |
| `CELERY_BROKER_URL` | Redis broker and progress Pub/Sub connection | Compose `redis` service |
| `QDRANT_URL`, `QDRANT_COLLECTION` | Qdrant endpoint and evidence collection name | Compose `qdrant`; `multimodal_rag_openai_large` |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_SECURE` | RustFS S3-compatible object storage | Compose `minio` service and local credentials |
| `MAX_UPLOAD_BYTES`, `MAX_IMAGE_BYTES` | Maximum upload sizes in bytes | 50 MiB; 20 MiB |
| `MAX_PDF_PAGES` | Maximum accepted PDF page count | `300` |
| `PDF_RENDER_DPI` | Resolution used for page images | `144` (allowed: 72–300) |
| `RETRIEVAL_LIMIT` | Maximum evidence records used to answer a document question | `8` |
| `APP_ENV`, `LOG_LEVEL` | Runtime environment name and log verbosity | `development`, `INFO` |

Keep `.env` private and do not commit it. Provider API keys stay in the backend; retrieved text and selected original images are sent to the configured chat/vision provider to generate answers and image descriptions.

## Use the application

### Upload a document

1. In **Add documents**, select or drop a PDF or image and choose **Process documents**.
2. Watch the row for its stage, percentage, and extraction/indexing counts. The browser receives updates over SSE; it does not repeatedly poll the status endpoint.
3. Wait for the document to show **Ready**, then ask a question in the chat panel.

General questions can be asked without uploading anything. For questions about a PDF, ask about the subject, page, table, chart, or other evidence in the uploaded source. For chart questions, ask to show the chart if you want the original retrieved image displayed with the answer.

The chat panel supports separate threads. **New chat** starts a thread; the history menu returns to a saved thread. Use **Memory** to view or remove long-term memories. **Forget all** deletes the signed-in user's long-term memories and conversation threads/messages; it does not delete uploaded documents.

### Data flow and storage

| Data | PostgreSQL | RustFS | Qdrant |
| --- | --- | --- | --- |
| Uploaded PDF/image | Document metadata, checksum, object key, ingestion state | Original uploaded file bytes | Not stored |
| Text chunk | Evidence content, document ID, page, kind, and source metadata | Not stored | Embedding plus searchable text/source payload |
| Table chunk | Extracted HTML/text and page/source metadata | Not stored | Embedding plus searchable table text/source payload |
| Image, chart, or rendered page | Description, MIME type, page/source metadata, and object key | Original extracted image or rendered page bytes | Embedding of its text description plus source pointer/metadata |
| Conversation/memory | Threads, messages, summaries, categorized long-term memory | Not stored | Long-term memory vectors and owner/type payload |

The PDF source is stored as an object in RustFS. PostgreSQL stores the searchable evidence records and links each visual record to its original image object. Qdrant stores vectors and payloads for retrieval; it is not the source of the original PDF or image bytes. Text, table, and image descriptions are embedded with OpenAI. Image bytes are retrieved from RustFS only when a relevant visual is selected for an answer or displayed as a citation.

### Ingestion and question-answer flow

1. `POST /api/v1/documents` validates the upload, writes its original bytes to RustFS, records document/job metadata in PostgreSQL, and queues a Celery task in Redis.
2. The worker extracts PDF text, tables, image blocks, and page renders. It records page-aware evidence in PostgreSQL and uploads visual assets to RustFS.
3. The selected vision/chat provider creates searchable descriptions for visual evidence. The OpenAI embedding model embeds text chunks, table text, and visual descriptions in batches. Qdrant stores these vectors with evidence IDs and metadata. A document becomes searchable after indexing completes.
4. The UI listens to `/api/v1/ingestions/{job_id}/events`. The API forwards progress events from Redis Pub/Sub as SSE. If an SSE connection finds a processing job whose recorded task is no longer known to Celery, it can requeue that orphaned job.
5. On each chat turn, an LLM router receives the question and conversation context and selects the general or RAG agent. The general agent answers from general knowledge and conversation memory; it does not receive retrieved document passages.
6. The RAG agent combines Qdrant semantic search with PostgreSQL lexical search, fetches original visual bytes from RustFS when relevant, and asks the configured model to answer from the retrieved evidence. The API validates citations against retrieved evidence and shows each source page once.
7. The user/assistant turn is saved to PostgreSQL. Recent turns and a rolling summary provide short-term context. An LLM extracts useful semantic, episodic, and procedural memories, which are saved in PostgreSQL and indexed in a dedicated Qdrant memory collection for cross-thread recall.

More detail is available in [docs/architecture.md](docs/architecture.md).

## Monitor and troubleshoot

Follow all service logs:

```bash
docker compose logs -f
```

Follow only API and worker logs:

```bash
docker compose logs -f api worker
```

Check whether Celery has active, reserved, or scheduled ingestion tasks:

```bash
docker compose exec worker celery -A app.worker.celery_app inspect active
docker compose exec worker celery -A app.worker.celery_app inspect reserved
docker compose exec worker celery -A app.worker.celery_app inspect scheduled
```

Upload returns a `job_id`. Check the current persisted status with:

```bash
curl http://localhost:8000/api/v1/ingestions/JOB_ID
```

To watch its SSE progress from a terminal:

```bash
curl -N http://localhost:8000/api/v1/ingestions/JOB_ID/events
```

`/api/v1/ingestions/{job_id}` is a one-time status snapshot; `/events` is the live stream used by the UI. If a job remains `processing`, check that the worker is running and inspect its logs and Celery task lists. A worker `ready` line means it can accept work; look for task-received or `ingestion.started` logs to confirm a job was claimed.

Some PDF warnings, such as missing font bounding-box metadata (`FontBBox`), may be emitted while parsing and can be non-fatal. The first high-resolution PDF parse may download layout/OCR model assets, including spaCy assets, and can take longer. Look for subsequent extraction or ingestion errors before concluding it is stuck. To capture detailed image-build output:

```bash
docker compose build --no-cache --progress=plain api worker
```

Inspect PostgreSQL from the Compose network:

```bash
docker compose exec postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Inspect Qdrant collections at <http://localhost:6333/dashboard> and uploaded objects in the RustFS console at <http://localhost:9001>. Credentials for the console are `S3_ACCESS_KEY` and `S3_SECRET_KEY` from `.env`.

## API reference

Interactive request schemas are available at <http://localhost:8000/docs>. Main endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | API health check |
| `POST` | `/api/v1/documents` | Upload and queue a PDF/image (`multipart/form-data`, field `file`) |
| `GET` | `/api/v1/ingestions/{job_id}` | Read current ingestion status |
| `GET` | `/api/v1/ingestions/{job_id}/events` | Stream ingestion status and progress events over SSE |
| `GET` | `/api/v1/evidence/{evidence_id}/image` | Display/download an original cited visual |
| `POST` | `/api/v1/chat` | Non-streaming text chat (`question`, optional `thread_id`) |
| `POST` | `/api/v1/chat/stream` | Streaming text chat as SSE events |
| `POST` | `/api/v1/chat/multimodal` | Non-streaming chat with image attachments |
| `POST` | `/api/v1/chat/multimodal/stream` | Streaming chat with image attachments |
| `POST`, `GET` | `/api/v1/threads` | Create/list conversation threads |
| `GET` | `/api/v1/threads/{thread_id}` | Read a thread and its messages |
| `GET` | `/api/v1/memories` | List long-term memory records |
| `DELETE` | `/api/v1/memories/{memory_id}` | Delete one memory |
| `DELETE` | `/api/v1/memory` | Delete all local memories and conversation threads |

Chat stream events include stages, generated token deltas, the final response, and completion/error events. Chat responses include an `answer`, `citations`, `insufficient_evidence`, and `thread_id`. Document citation page numbers are one-based. A `thread_id` must be sent on later requests to continue the same thread.

### API examples

Upload a PDF:

```bash
curl -F 'file=@report.pdf' http://localhost:8000/api/v1/documents
```

Ask a question and create a thread (the returned response includes its `thread_id`):

```bash
curl -H 'Content-Type: application/json' \
  -d '{"question":"What does the report say about ARR growth?"}' \
  http://localhost:8000/api/v1/chat
```

Continue a thread by passing its ID:

```bash
curl -H 'Content-Type: application/json' \
  -d '{"question":"Show me the chart for that trend.","thread_id":"THREAD_ID"}' \
  http://localhost:8000/api/v1/chat
```

Stream chat output:

```bash
curl -N -H 'Content-Type: application/json' \
  -d '{"question":"Summarize the report.","thread_id":"THREAD_ID"}' \
  http://localhost:8000/api/v1/chat/stream
```

Ask about an attached image:

```bash
curl -F 'question=What does this chart show?' \
  -F 'images=@chart.png' \
  http://localhost:8000/api/v1/chat/multimodal
```

## Develop, test, and update

### Run tests

Install the project and development dependencies in a Python environment supported by the project:

```bash
python -m pip install -e '.[dev]'
python -m pytest
```

The default tests use fake model, storage, and vector adapters; they do not call paid model APIs. PDF extraction dependencies may still require system packages when running outside Docker. Docker is the recommended way to run the full application stack.

### Apply code changes

Edit the files in this repository, then rebuild the services that contain those files:

```bash
docker compose up -d --build api worker
```

The API serves the browser UI, so rebuild `api` after changing files under `app/static/`. Rebuild `worker` after changing ingestion or task code. Rebuild both after changing shared application modules or dependencies. The API runs database migrations at startup. Normal code updates preserve PostgreSQL, Redis, Qdrant, RustFS, and model-cache volumes.

To rebuild all services after changing Compose configuration or dependencies:

```bash
docker compose up -d --build
```

### Change the embedding model

Qdrant vector dimensions must match the configured model and `EMBEDDING_DIMENSION`. A different model or dimension creates an incompatible vector space; re-embed existing evidence into a compatible Qdrant collection before querying it. Update `.env`, select an unused `QDRANT_COLLECTION` when the vector size changes, rebuild/restart the API and worker, then run:

```bash
docker compose exec api python -m app.reindex
```

`app.reindex` regenerates vectors from evidence text and descriptions already stored in PostgreSQL. It does not re-run PDF extraction or regenerate visual descriptions. The OpenAI API key is required for this operation.

### Stop or reset local data

Stop containers while keeping all persisted data:

```bash
docker compose down
```

Start them again:

```bash
docker compose up -d
```

To permanently delete **all** Compose volumes and start with empty databases, vector indexes, queues, uploaded objects, and cached models:

```bash
docker compose down -v --remove-orphans
docker compose up --build -d
```

The `down -v` command is destructive. It deletes uploaded PDFs/images and all persisted conversation, database, Redis, and Qdrant data. Do not use it for an ordinary code update.

## Security and deployment scope

This repository configures a single-machine development stack. The application currently has no user authentication or tenant isolation; local conversation memory uses one shared owner namespace. Keep the API bound to localhost and do not expose it to an untrusted network as-is. Before a shared or public deployment, implement authentication/authorization and per-user memory/document access, secure provider and storage credentials, TLS, backups, quotas, retention policies, monitoring, and network restrictions. Review which extracted text and images are sent to the configured model provider for your data requirements.
