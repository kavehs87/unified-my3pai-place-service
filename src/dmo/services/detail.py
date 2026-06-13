import asyncio
import json

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity, Media
from dmo.models.schemas import ClassificationItem, EntityDetail, MediaItem, OpenStatus


def _prosemirror_to_html(node: dict) -> str:
    """Convert ProseMirror JSON node to HTML string."""
    tag = node.get("type", "")
    content = node.get("content", [])
    text = node.get("text", "")
    marks = node.get("marks", [])

    if tag == "text":
        marked = text
        for mark in marks:
            mtype = mark.get("type", "")
            attrs = mark.get("attrs", {})
            if mtype == "bold":
                marked = f"<strong>{marked}</strong>"
            elif mtype == "italic":
                marked = f"<em>{marked}</em>"
            elif mtype == "link":
                marked = f'<a href="{attrs.get("href", "#")}">{marked}</a>'
            elif mtype == "code":
                marked = f"<code>{marked}</code>"
        return marked

    inner = "".join(_prosemirror_to_html(c) for c in content)

    if tag == "paragraph":
        return f"<p>{inner}</p>"
    elif tag == "heading":
        level = node.get("attrs", {}).get("level", 2)
        return f"<h{level}>{inner}</h{level}>"
    elif tag == "hardBreak":
        return "<br>"
    elif tag == "bulletList":
        items = "".join(f"<li>{_prosemirror_to_html(item)}</li>" for item in content)
        return f"<ul>{items}</ul>"
    elif tag == "orderedList":
        items = "".join(f"<li>{_prosemirror_to_html(item)}</li>" for item in content)
        return f"<ol>{items}</ol>"
    elif tag == "listItem":
        return inner
    elif tag == "blockquote":
        return f"<blockquote>{inner}</blockquote>"
    elif tag == "horizontalRule":
        return "<hr>"
    elif tag == "codeBlock":
        code_text = node.get("text", "")
        return f"<pre><code>{code_text}</code></pre>"
    return inner


def transform_description(description: str | None, description_format: str | None) -> str | None:
    """Transform description to HTML based on its format."""
    if not description:
        return description
    if description_format in (None, "html"):
        return description
    if description_format == "prosemirror":
        try:
            doc = json.loads(description)
            nodes = doc.get("content", []) if isinstance(doc, dict) else []
            return "".join(_prosemirror_to_html(n) for n in nodes)
        except (json.JSONDecodeError, TypeError):
            return description
    return description


async def get_detail(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> EntityDetail | None:
    entity_stmt = select(Entity).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
        col(Entity.is_active),
    )

    entity_result, media_result, classif_result = await asyncio.gather(
        session.exec(entity_stmt),
        _fetch_media_by_source(session, source, source_id),
        _fetch_classifications_by_source(session, source, source_id),
    )
    entity = entity_result.first()

    if not entity:
        return None

    detail = EntityDetail.model_validate(entity)
    detail.description = transform_description(detail.description, detail.description_format)
    detail.media = [MediaItem.model_validate(m) for m in media_result]
    detail.classifications = [ClassificationItem.model_validate(c) for c in classif_result]
    return detail


async def _fetch_media_by_source(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> list[Media]:
    stmt = (
        select(Media)
        .join(Entity, Media.entity_id == Entity.id)
        .where(
            col(Entity.source) == source,
            col(Entity.source_id) == source_id,
            col(Media.is_active),
        )
        .order_by(col(Media.sort_order))
    )
    result = await session.exec(stmt)
    return result.all()


async def _fetch_classifications_by_source(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> list[Classification]:
    stmt = (
        select(Classification)
        .join(Entity, Classification.entity_id == Entity.id)
        .where(
            col(Entity.source) == source,
            col(Entity.source_id) == source_id,
            col(Classification.is_active),
        )
    )
    result = await session.exec(stmt)
    return result.all()


async def get_open_status(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> OpenStatus | None:
    stmt = select(Entity.is_open, Entity.opens_at, Entity.closes_at).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
        col(Entity.is_active),
    )
    result = await session.exec(stmt)
    row = result.first()
    if not row:
        return None
    return OpenStatus(is_open=row[0], opens_at=row[1], closes_at=row[2])
