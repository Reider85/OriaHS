"""Seed the eval corpus referenced by the offline-eval dataset (C-07).

The nightly eval job (``app/eval/nightly.py``) measures Recall@10 / nDCG@10 of
the fusion strategies against ``eval/datasets/baseline_v1.jsonl``. That dataset
pins **explicit** ``relevant_doc_ids`` (UUIDs like
``550e8400-e29b-41d4-a716-446655440001``), and ``POST /index`` always generates
its own ``uuid4()`` — so the documents the judgements point at can never be
created through the public API. Without this script every nightly run scores
zero and the regression gate is meaningless.

This script therefore inserts those documents directly (same transaction
semantics as ``IndexingService.create``: document + ``search_outbox`` upsert
row), and additionally fills the tenant with low-selectivity noise so the
push-down filter planner (C-10, ``app/search/pushdown.py``) has a realistic
selectivity distribution to reason about.

Idempotent: existing ``document_id`` values are skipped, so re-running is safe.

Usage:
    python scripts/seed_eval_corpus.py [--dataset eval/datasets/baseline_v1.jsonl]
                                       [--filler 200] [--batch-size 100]
"""

import argparse
import asyncio
import hashlib
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.models import Document  # noqa: E402
from app.db.queries import documents as documents_queries  # noqa: E402
from app.db.queries import embedding_models  # noqa: E402
from app.db.queries import outbox as outbox_queries  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.eval.datasets import load_dataset  # noqa: E402

# 10% of the filler corpus gets category=ML — the selective category the
# push-down demo queries filter on. The rest keeps the filter unselective.
_SELECTIVE_CATEGORY = "ML"
_SELECTIVE_RATIO = 10
_FILLER_CATEGORIES = ("general", "support", "billing", "internal")
_FILLER_TAGS = ("seed", "filler", "eval-corpus")


def _content_hash(title: str, content: str) -> str:
    """Same digest as ``IndexingService.content_hash`` (ARCHITECT §3.3)."""
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def _eval_documents(dataset_path: str) -> dict[UUID, dict[str, Any]]:
    """Build ``doc_id -> document spec`` for every judged document."""
    judged: defaultdict[UUID, dict[str, Any]] = defaultdict(
        lambda: {"queries": []}
    )
    for query in load_dataset(dataset_path):
        for raw_id in query.relevant_doc_ids:
            entry = judged[UUID(raw_id)]
            entry["tenant_id"] = UUID(query.tenant_id)
            entry["language"] = query.language
            entry["queries"].append(query.query)

    documents: dict[UUID, dict[str, Any]] = {}
    for doc_id, entry in judged.items():
        queries = sorted(set(entry["queries"]))
        # The judged text is the concatenation of the queries that point at the
        # document — enough signal for both the lexical and the vector channel.
        title = queries[0]
        content = (
            f"{title}. Документ оценки {doc_id}. "
            f"Релевантен запросам: {', '.join(queries)}."
        )
        documents[doc_id] = {
            "tenant_id": entry["tenant_id"],
            "language": entry["language"],
            "title": title,
            "content": content,
            "tags": ["eval", "baseline_v1"],
            "attributes": {
                "source": "eval-dataset",
                "eval_query_count": len(queries),
            },
        }
    return documents


def _filler_documents(tenant_id: UUID, count: int) -> list[dict[str, Any]]:
    """Noise documents with a realistic category distribution (C-10)."""
    documents: list[dict[str, Any]] = []
    for i in range(count):
        selective = i % _SELECTIVE_RATIO == 0
        category = (
            _SELECTIVE_CATEGORY
            if selective
            else _FILLER_CATEGORIES[i % len(_FILLER_CATEGORIES)]
        )
        documents.append(
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "external_ref": f"seed-filler-{i:04d}",
                "title": f"Служебный документ {i} ({category})",
                "content": (
                    f"Служебный документ {i} категории {category}. "
                    "Описывает внутренние процессы, не связанные с поиском."
                ),
                "language": "ru",
                "tags": [*_FILLER_TAGS, f"category_{category}"],
                "attributes": {
                    "category": category,
                    "priority": "high" if i % 5 == 0 else "normal",
                    "source": "seed-filler",
                },
            }
        )
    return documents


async def _insert_document(
    session: AsyncSession,
    spec: dict[str, Any],
    model_name: str,
) -> bool:
    """Insert one document + outbox row. Returns False if it already existed."""
    doc_id: UUID = spec.get("id") or uuid4()
    existing = await documents_queries.get_by_id(session, doc_id)
    if existing is not None:
        return False

    title = str(spec["title"])
    content = str(spec["content"])
    document = Document(
        id=doc_id,
        tenant_id=spec["tenant_id"],
        external_ref=str(spec.get("external_ref") or f"eval-{doc_id}"),
        title=title,
        content=content,
        language=str(spec["language"]),
        tags=list(spec["tags"]),
        attributes=dict(spec["attributes"]),
        embedding_model=model_name,
        content_hash=_content_hash(title, content),
    )
    await documents_queries.add(session, document)
    await session.flush()
    await outbox_queries.add_upsert(session, doc_id, document.content_hash)
    return True


async def seed(dataset_path: str, filler: int, batch_size: int) -> int:
    """Seed the corpus. Returns the number of newly created documents."""
    eval_docs = _eval_documents(dataset_path)
    if not eval_docs:
        raise SystemExit(f"eval dataset {dataset_path} contains no relevant_doc_ids")

    tenant_ids: set[UUID] = {spec["tenant_id"] for spec in eval_docs.values()}
    filler_docs = (
        _filler_documents(next(iter(tenant_ids)), filler) if filler > 0 else []
    )

    created = 0
    async with async_session_factory() as session:
        model = await embedding_models.get_default_model(session)
        batch: list[dict[str, Any]] = [
            {"id": doc_id, **spec} for doc_id, spec in eval_docs.items()
        ]
        batch.extend(filler_docs)

        for start in range(0, len(batch), batch_size):
            for spec in batch[start : start + batch_size]:
                if await _insert_document(session, spec, model.name):
                    created += 1
            await session.commit()
            print(f"  seeded {created}/{len(batch)} documents", flush=True)

    print(
        f"seeded {created} documents "
        f"({len(eval_docs)} from {dataset_path}, {len(filler_docs)} filler) "
        f"for tenants: {', '.join(sorted(str(t) for t in tenant_ids))}"
    )
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default="eval/datasets/baseline_v1.jsonl",
        help="eval dataset JSONL (default: %(default)s)",
    )
    parser.add_argument(
        "--filler",
        type=int,
        default=200,
        help="extra noise documents per tenant for push-down selectivity (default: %(default)s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="documents per transaction (default: %(default)s)",
    )
    args = parser.parse_args()

    if not Path(args.dataset).exists():
        raise SystemExit(f"eval dataset not found: {args.dataset}")

    asyncio.run(seed(args.dataset, args.filler, args.batch_size))


if __name__ == "__main__":
    main()
