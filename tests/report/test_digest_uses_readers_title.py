"""The weekly digest calls a farm what the reader calls it.

The digest is a reader-facing view like the radar card and the dossier, so its
entries carry ``display_title``: the reader's own name when they gave one, the
advert's words otherwise. The property is built with the web fixtures' tuned
farmstead, the one the real scoring gates accept - a digest with no entries
would let this test pass by showing nothing.

The timestamps are taken from the wall clock rather than a frozen date, because
``build_report`` rescores through ``rescore_all`` without a ``now`` and a fixed
date would age the property out of the ranking (see CLAUDE.md, "a fixed test
clock and a wall-clock function make a time bomb").
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from hofradar.config import load_profile
from hofradar.db.enums import SourceRole
from hofradar.db.models import Source
from hofradar.report import build_report
from tests.web.conftest import add_observation, add_source_link, make_property

_LISTING_TITLE = "Hofstelle mit Stadel"


def _digest_titles(
    session: Session, make_source: Callable[..., Source], *, user_title: str | None
) -> dict[str, str]:
    now = datetime.now(UTC)
    source = make_source("testportal", role=SourceRole.PRIMARY, reliability=0.9)
    prop = make_property(
        session,
        public_id="HF-TITLE",
        canonical_title=_LISTING_TITLE,
        user_title=user_title,
        first_seen=now - timedelta(days=30),
        last_seen=now,
        last_verified=now,
    )
    add_source_link(session, prop, source)
    add_observation(session, prop, source, at=now - timedelta(days=1))

    data = build_report(session, load_profile(), since=now - timedelta(days=7), now=now)

    return {entry.public_id: entry.title for entry in data.entries}


def test_the_digest_uses_the_readers_name(
    session: Session, make_source: Callable[..., Source]
) -> None:
    titles = _digest_titles(session, make_source, user_title="Moarhof")

    assert titles == {"HF-TITLE": "Moarhof"}


def test_the_digest_falls_back_to_the_listing_title(
    session: Session, make_source: Callable[..., Source]
) -> None:
    titles = _digest_titles(session, make_source, user_title=None)

    assert titles == {"HF-TITLE": _LISTING_TITLE}
