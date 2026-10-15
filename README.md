# Multimodal RAG

FastAPI service for page-aware PDF and image ingestion, multimodal retrieval, general questions, and grounded answers with citations. A LangGraph router sends ordinary questions to a general-answer agent and indexed-corpus questions to a document RAG agent. Unstructured partitions PDFs into semantic elements, tables, OCR text, and image blocks; LangChain Documents and text splitters preserve metadata as text is chunked. Ingestion uses the configured vision provider to describe visual evidence; retrieval indexes text, table representations, and image summaries while keeping the original assets in object storage.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `MODEL_PROVIDER` to `openai`, `groq`, or `gemini`, and set the matching key. `OPENAI_API_KEY` is required for embeddings even when another provider is selected; `GROQ_API_KEY` or `GEMINI_API_KEY` is additionally needed when choosing those providers for vision/chat. The selected model setting can be changed for provider account/model availability. Keys stay on the server.
3. Run `docker compose up --build`.
4. Open `http://localhost:8000` for the upload and chat interface. Open `http://localhost:8000/docs` for the API reference, or `http://localhost:9001` for the RustFS development console.

In the web interface, you can ask general questions immediately. To ask about your own material, add PDFs or images and choose **Process documents**, then wait for the document's status to become **Ready**. The LangGraph router selects a general answer for ordinary knowledge questions and the RAG agent for questions about indexed sources. Attaching an image sends the question and image through the multimodal RAG agent. Document answers include filenames, page numbers, and evidence types in their citations.

The chat panel keeps conversations in threads. Use **New chat** to start a separate conversation or choose a prior one from the history menu. Thread history survives browser reloads. Ingestion status and progress reach the browser over Server-Sent Events (SSE), without repeated browser polling. Chat answer text streams to the UI as the model generates it. PDF embeddings and Qdrant writes are batched, and image descriptions run with limited concurrency. A thread keeps recent turns and a rolling summary for short-term context; semantic facts/preferences, episodic events/decisions, and procedural instructions/workflows are extracted by an LLM from each interaction, stored in PostgreSQL, embedded into a dedicated Qdrant memory collection, and recalled across threads. Open **Memory** to review or delete individual records, or use **Forget all** to erase all memories and conversation threads. Memory helps resolve references and personalize answers; it is not treated as evidence about uploaded documents.

The default embedding model is OpenAI `text-embedding-3-large`; it uses `OPENAI_API_KEY` and produces 3,072-dimension vectors by default. OpenAI currently documents small and large embedding tiers, not a medium tier. The model embeds extracted text, table representations, and generated image descriptions; the vision/chat provider still describes images and generates answers. Unstructured's local high-resolution PDF partitioning may download layout model weights on first use and is CPU-intensive. Changing the embedding model requires a matching Qdrant collection and re-embedding because vector spaces are not interchangeable.

The Compose credentials and exposed service ports are for local development. Use managed credentials, TLS, access control, backups, and network restrictions before exposing the stack beyond a trusted machine. Database schema changes are applied by Alembic at API startup.

## End-to-end flow

1. `POST /api/v1/documents` accepts a PDF or standalone PNG/JPEG/WebP. It validates size, extension, MIME type, and content signature; stores the source object; and queues an idempotent ingestion job.
2. Unstructured partitions PDF pages into text, tables, OCR text, and image elements while preserving page metadata. PyMuPDF renders every page to additionally capture vector charts/diagrams. The ingestion service retains source filename, document ID, page number, MIME type, and original object key.
3. Image assets and rendered pages are sent to the selected vision model for searchable textual descriptions. Text chunks, Markdown table representations, and visual summaries are embedded with OpenAI and indexed in Qdrant. Evidence records and job state are persisted in PostgreSQL. Vector records are not searchable until the document finishes indexing.
4. On chat, `LangGraphChatRouter` runs a classifier node with the question, indexed filenames, and conversation memory. It dispatches to `GeneralAnswerAgent` for ordinary knowledge questions or `DocumentRAGAgent` for questions about indexed material. User-attached images and visual requests go to the RAG agent.
5. The LLM router classifies every turn using a small Qdrant/PostgreSQL evidence probe, completed source filenames, attached-image presence, and conversation context. The general agent answers from the selected LLM's general knowledge and can use memory for personal preferences or conversational references; it receives no retrieved document passages and does not cite document sources. The RAG agent uses Qdrant semantic retrieval plus PostgreSQL lexical search, fetches original image bytes from RustFS when needed, and asks the vision/chat model to answer only from retrieved evidence.
6. The API validates RAG citations against the retrieved evidence allowlist and returns page-level citations. When requested document evidence is missing, it returns an insufficient-evidence response instead of inventing source facts.

### LangGraph agents

`app/query/graph.py` defines a three-node graph: a router, a general-answer agent, and a document RAG agent. The configured chat model classifies every turn using completed source filenames, retrieved candidate snippets, attached-image presence, and saved conversation context. Each request includes the current thread's short-term memory and recalled long-term memory. The graph is compiled once during API startup; PostgreSQL remains the durable memory store, so memory survives API restarts and switching threads. LangGraph state is request-scoped and does not replace the database memory system.

## Conversation memory

- `conversation_threads` stores thread IDs, titles, and rolling summaries. `conversation_messages` stores user and assistant turns, citations, and timestamps.
- Each turn includes the most recent eight exchanges. As older turns accumulate, a model-generated summary preserves the useful thread context.
- The LangGraph router and both answer agents receive the short-term context and recalled long-term memories for each turn. An LLM can acknowledge a user statement such as “My name is Nishad Anil” and extract it as a semantic fact for PostgreSQL.
- When a thread has history, an LLM decides whether a question needs context and rewrites it as a standalone retrieval query when needed. The rewritten query is used for Qdrant semantic search and PostgreSQL lexical search; the original question is still sent to the answer model.
- Semantic facts/preferences, episodic events/decisions, and procedural instructions/workflows are stored as categorized records in PostgreSQL `long_term_memories`. Their vectors and owner-scoped payloads are stored in the separate `<QDRANT_COLLECTION>_memory` collection. Retrieval searches each category so one type cannot crowd out the others; relevant memories can be recalled across threads.
- Threads are managed with `POST /api/v1/threads`, `GET /api/v1/threads`, and `GET /api/v1/threads/{thread_id}`. Chat accepts an optional `thread_id`, and its response returns the active thread ID.
- `GET /api/v1/memories` lists saved records, `DELETE /api/v1/memories/{memory_id}` removes one record, and `DELETE /api/v1/memory` forgets all long-term memories and deletes all local conversation threads/messages so old details cannot be recalled from thread history.

This app currently has no authentication, so memory uses one local owner namespace. That matches the local Compose setup, which binds the API to loopback. Before exposing it to multiple users, add authentication and use the authenticated subject as `owner_id` for every thread and memory operation; otherwise users would share memory.

## API examples

Upload a document:

```bash
curl -F 'file=@report.pdf' http://localhost:8000/api/v1/documents
```

Poll the returned job ID:

```bash
curl http://localhost:8000/api/v1/ingestions/JOB_ID
```

Ask from the indexed corpus:

```bash
curl -H 'Content-Type: application/json' \
  -d '{"question":"What was the ARR growth rate?"}' \
  http://localhost:8000/api/v1/chat
```

Ask with an image and text:

```bash
curl -F 'question=What does this chart show?' \
  -F 'images=@chart.png' \
  http://localhost:8000/api/v1/chat/multimodal
```

Chat response shape:

```json
{
  "answer": "...",
  "citations": [
    {"document_id":"...","filename":"report.pdf","page_number":3,"evidence_id":"...","evidence_type":"table"}
  ],
  "insufficient_evidence": false,
  "thread_id": "..."
}
```

For later curl turns, send the returned ID as `{"question":"...","thread_id":"..."}` so they stay in the same conversation. If omitted, the API starts a new thread for that request.

User-uploaded images are request-scoped evidence and can be cited as `user_image` with no document ID or page number. Document citations use one-based PDF page numbers.

## Development and tests

After changing embedding models, rebuild the active Qdrant collection from the evidence already stored in PostgreSQL:

```bash
docker compose up --build -d
docker compose exec api python -m app.reindex
```

This sends each evidence text/description to OpenAI for a new embedding and writes vectors to the configured collection. It preserves the source PDFs and image assets.

Install Docker Desktop and an API key for one supported provider, then follow the Compose instructions above. Run the test suite in an environment with project dependencies installed:

```bash
python -m pip install -e '.[dev]'
pytest
```

Unit and integration tests use fake provider/storage/vector adapters and do not consume model credits. The end-to-end API test uses the same fakes. Live provider checks are deliberately not part of the default test suite.

## Architecture

See [docs/architecture.md](docs/architecture.md) for component responsibilities, persistence boundaries, failure handling, and configuration.
