# Architecture

## Components

- **FastAPI API and browser UI:** the API provides file validation, upload/job creation, status polling, text chat, and image-plus-text chat. A same-origin static UI at `/` supports general questions even when no document is uploaded, plus corpus questions with citations. Routes delegate chat turns to a compiled LangGraph workflow.
- **Celery worker:** independently processes durable ingestion jobs. Failed tasks retry with backoff; final failures are stored in the job row.
- **PDF extraction and chunking:** Unstructured's local high-resolution partitioner returns semantic text, table HTML, OCR text, image payloads, and page metadata. PyMuPDF renders each page to capture vector diagrams/charts. LangChain `Document` and `RecursiveCharacterTextSplitter` provide metadata-aware text chunks; the application keeps table boundaries and page-scoped citations.
- **Model provider:** one server-wide `MODEL_PROVIDER` selects OpenAI, Groq, or Gemini vision/text generation. Provider classes share a small interface for image summaries and grounded answers.
- **Embeddings and vector index:** OpenAI `text-embedding-3-large` embeddings feed Qdrant (3,072 dimensions by default). PostgreSQL full-text search complements semantic retrieval for exact labels and values; reciprocal rank fusion combines the two result lists.
- **Metadata and assets:** PostgreSQL stores document, job, and evidence rows. RustFS provides the local S3-compatible object store for source uploads and original image/page-render objects. Vector payloads retain evidence IDs and source metadata, not image bytes.

## Ingestion consistency

The API validates and stores the original before creating a queued job. The worker claims queued jobs with its Celery task ID, so duplicate task deliveries cannot both take ownership; a redelivered task with the same ID can resume after a worker loss. The worker resets partial vector state on retry and rebuilds evidence with deterministic IDs derived from source/page/type/content. Each vector is written with `ready=false`; after extraction, summaries, embeddings, and metadata writes succeed, the worker marks the document’s vectors ready. Query results are also checked against completed document rows. This prevents partially processed documents from appearing in retrieval.

An upload checksum is unique so repeat submissions reuse the existing job/document. Status values are `queued`, `processing`, `completed`, and `failed`. Provider, database, storage, and vector failures are logged by the task framework and surfaced as a safe job error after retries.

## Query grounding

The RAG query service fuses semantic and lexical results and formats each evidence item with its evidence ID, filename, page, type, and extracted content. Retrieved image summaries resolve to their original RustFS object and are attached as image inputs alongside user-uploaded images. Image citations include an endpoint that displays the original image directly in the browser. The RAG model must produce JSON containing cited evidence IDs. The service discards citations outside the retrieved allowlist and requires valid citations for document-grounded answers. General-agent answers do not make document claims or return document citations. User images receive request-scoped citation IDs and do not claim a document page.

## LangGraph chat routing

`LangGraphChatRouter` has a router node and two answer nodes. The router sees the user question, completed indexed source names, a small Qdrant similarity probe, and supplied conversation memory, then selects `GeneralAnswerAgent` or `DocumentRAGAgent`. General questions go to the configured chat model without receiving retrieved document passages and return no document citations. Questions about the corpus, its sources, or attached visuals go to the RAG agent, which uses hybrid retrieval and validates citations. If a question explicitly asks for source content and no evidence is retrieved, the RAG agent returns insufficient evidence.

Conversation memory stays in PostgreSQL and Qdrant, outside the graph's transient state. Account passwords are Argon2 hashed; the browser receives a signed, expiring, HttpOnly session cookie that can be revoked by advancing the account session version. The API scopes threads, long-term memories, uploads, ingestion status, evidence images, and document retrieval to the authenticated user. Pre-account documents remain unassigned and are hidden from signed-in accounts unless explicitly assigned by an administrator. The API loads recent turns and summaries for short-term context and retrieves semantic, episodic, and procedural memories from Qdrant using the authenticated owner ID before graph invocation. Both agent paths receive that memory. The user question and final answer are persisted after graph completion; this keeps history available across thread switches and API restarts without requiring an in-process LangGraph checkpointer.

## Provider and storage changes

Provider choice and API keys are server-side environment settings. Provider adapters normalize image-summary and answer generation calls. OpenAI embeddings use `OPENAI_API_KEY` and are independent of the vision/chat provider selection. `ObjectStorage`, `EmbeddingModel`, and `VectorIndex` are interfaces around the S3-compatible RustFS store, OpenAI embeddings, and Qdrant, so alternate object stores, embedding models, or vector databases can be introduced without changing query/ingestion orchestration. Replacing the embedding model requires a compatible vector collection and complete re-embedding; `python -m app.reindex` rebuilds vectors from stored PostgreSQL evidence.

LangChain is used at the document/chunk boundary rather than as a generic RAG chain. The explicit query service still controls hybrid retrieval, citation allowlists, original-image resolution, and insufficient-evidence behavior, which are application-specific grounding requirements.

## Operational constraints

- A visual summary is a retrieval aid; final answers see the original retrieved image. PDF vector graphics are represented with page renders, which can increase image-summary calls and storage use.
- Groq requires each image dimension to be at least 32 pixels. Its provider adapter upscales only smaller outbound request copies with nearest-neighbor sampling; the original image bytes remain stored and are used as the source for answer-time image inputs.
- OCR and table extraction depend on document quality and layout. Extraction uncertainty is preserved as evidence limits; the answer path declines unsupported claims.
- Unstructured's local `hi_res` path improves layout/table/image extraction but increases image size, model download time, and CPU/RAM use compared with basic PDF text extraction. This deployment keeps document bytes local; the configured vision provider still receives extracted image content for visual summaries.
- Multimodal image limits follow the selected provider: Groq accepts at most two user images and three total images so one retrieved document visual can be included; OpenAI/Gemini accept up to three user images and eight total images. The current text chunker is character-based and page-scoped.
- The initial deployment is single-machine Docker Compose. Authentication, tenant isolation, quotas, object retention policy, distributed tracing, and horizontal autoscaling are deployment work before a public multi-tenant launch.
