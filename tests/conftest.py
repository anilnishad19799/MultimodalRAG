import json

import fitz


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)

    def get(self, key):
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)


class FakeEmbedder:
    def embed(self, text):
        return [1.0] + [0.0] * 383


class FakeVectorIndex:
    def __init__(self):
        self.records = {}

    def upsert(self, record_id, vector, payload):
        self.records[record_id] = payload

    def search(self, vector, limit):
        return list(self.records.values())[:limit]

    def delete_document(self, document_id):
        self.records = {key: row for key, row in self.records.items() if row["document_id"] != document_id}

    def activate_document(self, document_id):
        for row in self.records.values():
            if row["document_id"] == document_id:
                row["ready"] = True


class FakeProvider:
    def __init__(self):
        self.received_images = []

    def summarize_image(self, image, mime_type, source_hint):
        return f"Chart or document image from {source_hint}; visual evidence summary."

    def answer(self, prompt, images):
        self.received_images = images
        import re

        if "You are the routing agent in a LangGraph" in prompt:
            question = prompt.split("Latest user question:", 1)[-1].casefold()
            route = "rag" if any(term in question for term in (
                "arr", "report", "document", "pdf", "chart", "table", "retention",
            )) else "general"
            return json.dumps({"route": route})

        if "GENERAL_KNOWLEDGE_QUESTION" in prompt:
            return json.dumps({
                "answer": "A general knowledge answer.", "citations": [], "insufficient_evidence": False,
            })

        ids = re.search(r"Allowed evidence IDs: (\[.*?\])", prompt).group(1)
        citations = json.loads(ids)
        return json.dumps({
            "answer": "The supplied evidence includes the requested information.",
            "citations": citations[:1],
            "insufficient_evidence": False,
        })


def make_pdf(text: str = "Quarterly ARR growth was 18 percent.") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text * 4)
    result = document.tobytes()
    document.close()
    return result
