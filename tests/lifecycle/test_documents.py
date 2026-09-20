"""``ingest._record_documents``: one ``Document`` row per (property, url).

A crawl that reads the same exposé every week must not stack up copies, but a
new revision of it - the same URL, more pages, a new title - updates what is
on file rather than being silently dropped. See
``src/hofradar/lifecycle/ingest.py::_record_documents``.
"""

from __future__ import annotations

from hofradar.contracts import DocumentRef
from hofradar.db.enums import SourceRole
from hofradar.db.models import Document
from hofradar.lifecycle import ingest


def test_one_document_ref_creates_one_document_row(db_session, make_source, make_listing):
    source = make_source("denkmalboerse", role=SourceRole.PRIMARY)
    ref = DocumentRef(
        kind="expose", url="https://example.test/expose.pdf", title="Exposé", page_count=5
    )
    listing = make_listing(source_key=source.key, documents=[ref])

    prop, _ = ingest(db_session, listing, source=source, run_id=1)

    rows = db_session.query(Document).filter_by(property_id=prop.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.property_id == prop.id
    assert row.source_id == source.id
    assert row.kind == "expose"
    assert row.document_url == "https://example.test/expose.pdf"
    assert row.title == "Exposé"
    assert row.page_number == 5
    assert row.local_path is None


def test_reingesting_the_same_url_updates_in_place_not_a_second_row(
    db_session, make_source, make_listing
):
    source = make_source("denkmalboerse", role=SourceRole.PRIMARY)
    url = "https://example.test/objekt/1"
    first_listing = make_listing(
        source_key=source.key,
        url=url,
        documents=[
            DocumentRef(
                kind="expose", url="https://example.test/expose.pdf", title="Alt", page_count=3
            )
        ],
    )
    prop, _ = ingest(db_session, first_listing, source=source, run_id=1)

    second_listing = make_listing(
        source_key=source.key,
        url=url,
        documents=[
            DocumentRef(
                kind="expose",
                url="https://example.test/expose.pdf",
                title="Neu (mehr Seiten)",
                page_count=7,
            )
        ],
    )
    ingest(db_session, second_listing, source=source, run_id=2)

    rows = db_session.query(Document).filter_by(property_id=prop.id).all()
    assert len(rows) == 1
    assert rows[0].title == "Neu (mehr Seiten)"
    assert rows[0].page_number == 7


def test_a_document_ref_with_an_empty_url_is_skipped(db_session, make_source, make_listing):
    source = make_source("denkmalboerse", role=SourceRole.PRIMARY)
    listing = make_listing(source_key=source.key, documents=[DocumentRef(kind="expose", url="")])

    prop, _ = ingest(db_session, listing, source=source, run_id=1)

    assert db_session.query(Document).filter_by(property_id=prop.id).count() == 0


def test_two_different_urls_create_two_rows(db_session, make_source, make_listing):
    source = make_source("denkmalboerse", role=SourceRole.PRIMARY)
    listing = make_listing(
        source_key=source.key,
        documents=[
            DocumentRef(kind="expose", url="https://example.test/a.pdf"),
            DocumentRef(kind="expose", url="https://example.test/b.pdf"),
        ],
    )

    prop, _ = ingest(db_session, listing, source=source, run_id=1)

    rows = db_session.query(Document).filter_by(property_id=prop.id).all()
    assert {row.document_url for row in rows} == {
        "https://example.test/a.pdf",
        "https://example.test/b.pdf",
    }
    assert len(rows) == 2
