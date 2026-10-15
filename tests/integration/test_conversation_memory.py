import json
import re

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.container import Services
from app.db.models import (
    Base, ConversationMessage, ConversationThread, DocumentRecord, EvidenceRecord, IngestionJob, LongTermMemory,
)
from app.main import create_app


class MemoryEmbedder:
    def __init__(self):
        self.texts = []

    def embed(self, text):
        self.texts.append(text)
        return [1.0, 0.0, 0.0]


class MemoryVectorIndex:
    def __init__(self):
        self.documents = []
        self.memories = {}

    def upsert(self, record_id, vector, payload):
        self.documents.append(payload)

    def search(self, vector, limit):
        return self.documents[:limit]

    def delete_document(self, document_id):
        self.documents = [item for item in self.documents if item.get("document_id") != document_id]

    def activate_document(self, document_id):
        return None

    def upsert_memory(self, memory_id, vector, payload):
        self.memories[memory_id] = payload

    def search_memories(self, vector, owner_id, limit=5, memory_type=None):
        rows = [item for item in self.memories.values() if item["owner_id"] == owner_id]
        if memory_type == "semantic":
            rows = [item for item in rows if item["memory_type"] in {"semantic", "user_context"}]
        elif memory_type:
            rows = [item for item in rows if item["memory_type"] == memory_type]
        return [{**item, "score": 0.9} for item in rows[:limit]]

    def delete_memory(self, memory_id):
        self.memories.pop(memory_id, None)

    def delete_memories(self, owner_id):
        self.memories = {
            key: item for key, item in self.memories.items() if item["owner_id"] != owner_id
        }


class MemoryProvider:
    def __init__(self):
        self.prompts = []

    def summarize_image(self, image, mime_type, source_hint):
        return "An image."

    def answer(self, prompt, images):
        self.prompts.append(prompt)
        if "You are the routing agent in a LangGraph" in prompt:
            question = prompt.split("Latest user question:", 1)[-1].casefold()
            route = "rag" if any(term in question for term in (
                "arr", "retention", "report", "document", "pdf", "chart", "table", "what about",
            )) else "general"
            return json.dumps({"route": route})
        if "GENERAL_KNOWLEDGE_QUESTION" in prompt:
            question = prompt.rsplit("Question:", 1)[-1].casefold()
            if "sky blue" in question:
                answer = "The sky looks blue because air molecules scatter shorter blue wavelengths more strongly."
            elif "answer style" in question:
                answer = "You prefer concise answers."
            elif "my name is anil sharma" in question:
                answer = "Got it. I’ll remember that your name is Anil Sharma."
            elif "last question" in question and "my name is anil sharma" in prompt.casefold():
                answer = "Your previous question was: ‘My name is Anil Sharma.’"
            elif "last 3" in question or "last three" in question:
                answer = "Your last three questions were the three questions shown in this thread’s recent messages."
            elif "my name" in question and "anil sharma" in prompt.casefold():
                answer = "Your name is Anil Sharma."
            elif "my name" in question:
                answer = "You haven’t told me your name in the saved conversations I can access."
            else:
                answer = "A general answer."
            return json.dumps({"answer": answer, "citations": [], "insufficient_evidence": False})
        if "No document evidence was retrieved" in prompt:
            return json.dumps({
                "answer": "The sky looks blue because air molecules scatter shorter blue wavelengths more strongly.",
                "citations": [], "insufficient_evidence": False,
            })
        if "You prepare a search query for a conversational assistant" in prompt:
            return json.dumps({"search_query": "Acme retention 2023"})
        if "Extract only useful information" in prompt:
            if "My name is Anil Sharma." in prompt:
                return json.dumps({"memories": [
                    {"type": "semantic", "content": "The user's name is Anil Sharma."},
                ]})
            return json.dumps({"memories": [
                {"type": "semantic", "content": "The user prefers concise answers."},
                {"type": "episodic", "content": "The user is building the Falcon dashboard."},
                {"type": "procedural", "content": "For chart questions, show the original chart and cite its page."},
            ]})
        if "Update the conversation memory summary" in prompt:
            return json.dumps({"summary": "The user is discussing Acme retention and prefers concise answers."})
        ids = re.search(r"Allowed evidence IDs: (\[.*?\])", prompt).group(1)
        return json.dumps({
            "answer": "The retrieved source says ARR growth was 18%.",
            "citations": json.loads(ids)[:1],
            "insufficient_evidence": False,
        })


def make_memory_app():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider, vector, embedder = MemoryProvider(), MemoryVectorIndex(), MemoryEmbedder()
    services = Services(provider, object(), embedder, vector)
    app = create_app(Settings(database_url="sqlite://"), services, factory)
    return app, factory, provider, vector, embedder


def test_threads_recall_all_memory_types_across_threads_and_can_forget_everything():
    app, factory, provider, vector, embedder = make_memory_app()
    with TestClient(app) as client:
        first = client.post("/api/v1/threads", json={"title": "First thread"}).json()
        saved = client.post("/api/v1/chat", json={
            "thread_id": first["id"],
            "question": "Remember I prefer short answers for my Falcon dashboard.",
        })
        assert saved.status_code == 200
        assert saved.json()["thread_id"] == first["id"]

        messages = client.get(f"/api/v1/threads/{first['id']}").json()["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]

        memories = client.get("/api/v1/memories").json()
        assert {item["memory_type"] for item in memories} == {"semantic", "episodic", "procedural"}
        assert {item["memory_type"] for item in vector.memories.values()} == {
            "semantic", "episodic", "procedural",
        }

        follow_up = client.post("/api/v1/chat", json={
            "thread_id": first["id"], "question": "What about 2023?",
        })
        assert follow_up.status_code == 200
        assert "Acme retention 2023" in embedder.texts

        second = client.post("/api/v1/threads", json={"title": "Second thread"}).json()
        recalled = client.post("/api/v1/chat", json={
            "thread_id": second["id"], "question": "What answer style do I prefer?",
        })
        assert recalled.status_code == 200
        assert recalled.json()["answer"] == "You prefer concise answers."
        assert recalled.json()["citations"] == []
        assert "concise answers" in provider.prompts[-1]

        episodic = next(item for item in memories if item["memory_type"] == "episodic")
        removed = client.delete(f"/api/v1/memories/{episodic['id']}")
        assert removed.status_code == 204
        assert episodic["id"] not in vector.memories
        assert all(item["memory_type"] != "episodic" for item in client.get("/api/v1/memories").json())

        forgotten = client.delete("/api/v1/memory")
        assert forgotten.status_code == 200
        assert forgotten.json() == {"deleted_memories": 2, "deleted_threads": 2}
        assert client.get("/api/v1/memories").json() == []
        assert client.get("/api/v1/threads").json() == []
        assert vector.memories == {}

        with factory() as session:
            assert session.scalars(select(ConversationThread)).all() == []
            assert session.scalars(select(ConversationMessage)).all() == []
            assert session.scalars(select(LongTermMemory)).all() == []


def test_short_term_summary_is_written_without_summarizing_the_newest_turns():
    app, factory, provider, _, _ = make_memory_app()
    from app.query.memory import ConversationMemory

    with TestClient(app):
        with factory() as session:
            thread = ConversationThread(id="thread-summary", owner_id="local", title="Summary check")
            session.add(thread)
            session.flush()
            for index in range(28):
                session.add(ConversationMessage(
                    id=f"message-{index:02d}", thread_id=thread.id,
                    role="user" if index % 2 == 0 else "assistant", content=f"Turn detail {index}", citations=[],
                ))
            session.commit()
            memory = ConversationMemory(session, app.state.services)
            memory._refresh_summary(thread.id)
            session.refresh(thread)
            assert thread.summary == "The user is discussing Acme retention and prefers concise answers."
            assert thread.summary_message_count == 12
            summary_prompt = provider.prompts[-1]
            assert "Turn detail 0" in summary_prompt
            assert "Turn detail 11" in summary_prompt
            assert "Turn detail 12" not in summary_prompt


def test_document_answer_and_citations_are_persisted_in_the_selected_thread():
    app, factory, _, vector, _ = make_memory_app()
    with factory() as session:
        session.add(DocumentRecord(
            id="doc-arr", filename="arr-report.pdf", content_type="application/pdf",
            checksum="arr-checksum", object_key="documents/arr-report.pdf", status="completed",
        ))
        session.add(EvidenceRecord(
            id="evidence-arr", document_id="doc-arr", kind="text", page_number=3,
            filename="arr-report.pdf", content="ARR growth was 18%.", object_key=None,
        ))
        session.commit()
    vector.documents.append({
        "evidence_id": "evidence-arr", "document_id": "doc-arr", "filename": "arr-report.pdf",
        "page_number": 3, "kind": "text", "content": "ARR growth was 18%.", "object_key": None,
    })

    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        response = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "What was ARR growth?",
        })
        assert response.status_code == 200
        body = response.json()
        assert "18%" in body["answer"]
        assert body["citations"][0]["evidence_id"] == "evidence-arr"
        restored = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert restored["messages"][1]["citations"][0]["evidence_id"] == "evidence-arr"


def test_personal_history_questions_use_saved_messages_instead_of_document_retrieval():
    app, _, _, vector, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        first = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "My name is Anil Sharma.",
        })
        assert first.status_code == 200
        assert first.json()["answer"] == "Got it. I’ll remember that your name is Anil Sharma."
        assert first.json()["insufficient_evidence"] is False
        assert any(
            item["content"] == "The user's name is Anil Sharma."
            and item["memory_type"] == "semantic"
            for item in client.get("/api/v1/memories").json()
        )

        last_question = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "What was my last question?",
        }).json()
        assert last_question["answer"] == "Your previous question was: “My name is Anil Sharma.”"
        assert last_question["citations"] == []
        assert last_question["insufficient_evidence"] is False

        name = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "What is my name?",
        }).json()
        assert name["answer"] == "Your name is Anil Sharma."
        assert name["citations"] == []
        assert name["insufficient_evidence"] is False
        assert vector.documents == []


def test_last_three_questions_is_routed_to_general_agent_with_thread_history():
    app, _, provider, vector, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        prior_questions = [
            "Why does the PDF parser warn about FontBBox?",
            "What is en_core_web_sm used for?",
            "How can I check whether the worker finished processing?",
        ]
        for question in prior_questions:
            response = client.post("/api/v1/chat", json={
                "thread_id": thread["id"], "question": question,
            })
            assert response.status_code == 200

        response = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "Give me my last 3 questions.",
        })
        assert response.status_code == 200
        assert response.json()["insufficient_evidence"] is False
        assert response.json()["citations"] == []
        assert "last three questions" in response.json()["answer"]
        route_prompt = next(prompt for prompt in provider.prompts
                            if "You are the routing agent" in prompt
                            and "Give me my last 3 questions." in prompt)
        assert "Reason about what information source the user's intent requires" in route_prompt
        answer_prompt = next(prompt for prompt in provider.prompts
                             if "GENERAL_KNOWLEDGE_QUESTION" in prompt
                             and "Give me my last 3 questions." in prompt)
        assert all(question in answer_prompt for question in prior_questions)
        assert vector.documents == []


def test_unknown_name_is_reported_as_unshared_personal_context():
    app, _, _, _, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        response = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "What is my name?",
        }).json()
        assert "haven’t told me your name" in response["answer"]
        assert response["insufficient_evidence"] is False


def test_general_knowledge_question_is_answered_without_document_evidence():
    app, _, provider, _, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        response = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "Why is the sky blue?",
        }).json()
        assert "scatter shorter blue wavelengths" in response["answer"]
        assert response["citations"] == []
        assert response["insufficient_evidence"] is False
        assert any("GENERAL_KNOWLEDGE_QUESTION" in prompt for prompt in provider.prompts)


def test_general_answer_parser_does_not_apply_rag_evidence_flag():
    from app.query.service import QueryService

    parsed = QueryService._parse_general_answer(json.dumps({
        "answer": "Your last questions were about PDF fonts, spaCy, and worker status.",
        "citations": [],
        "insufficient_evidence": True,
    }))
    assert parsed["answer"].startswith("Your last questions were")


def test_streaming_chat_emits_answer_tokens_and_final_response():
    app, _, _, _, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        response = client.post("/api/v1/chat/stream", json={
            "thread_id": thread["id"], "question": "Why is the sky blue?",
        })
        assert response.status_code == 200
        assert "event: stage" in response.text
        assert "event: token" in response.text
        assert '"answer"' in response.text
        assert "event: result" in response.text
        assert "event: done" in response.text


def test_ingestion_sse_sends_current_progress_snapshot(monkeypatch):
    app, factory, _, _, _ = make_memory_app()
    with factory() as session:
        document = DocumentRecord(
            id="doc-sse", filename="sse.pdf", content_type="application/pdf",
            checksum="sse-checksum", object_key="documents/sse.pdf", status="completed",
        )
        session.add(document)
        session.add(IngestionJob(
            id="job-sse", document_id="doc-sse", status="completed",
            progress_stage="completed", progress_percent=100,
            progress_details={"pages": 2, "text_chunks": 4, "table_chunks": 1,
                              "image_records": 2, "total_records": 7},
        ))
        session.commit()

    class FakePubSub:
        def subscribe(self, channel):
            self.channel = channel

        def close(self):
            pass

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

        def close(self):
            pass

    monkeypatch.setattr(
        "app.api.routes.Redis",
        type("RedisStub", (), {"from_url": staticmethod(lambda url: FakeRedis())}),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/ingestions/job-sse/events")
        assert response.status_code == 200
        assert "event: progress" in response.text
        assert '"progress_percent": 100' in response.text
        assert '"table_chunks": 1' in response.text


def test_document_question_without_retrieved_evidence_stays_unanswered():
    app, _, provider, _, _ = make_memory_app()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()
        response = client.post("/api/v1/chat", json={
            "thread_id": thread["id"], "question": "What does the uploaded report say about revenue?",
        }).json()
        assert response["insufficient_evidence"] is True
        assert any("You are the routing agent in a LangGraph" in prompt for prompt in provider.prompts)

