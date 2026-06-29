import asyncio
import base64
import json
import os

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.admin.auth import verify_admin
from dmo.admin.llm_client import LLMClient
from dmo.admin.script_runner import (
    create_run,
    get_run,
    make_progress_callback,
    update_run,
)
from dmo.admin.settings_manager import load_settings, save_settings
from dmo.admin_scripts.registry import get_script, list_scripts
from dmo.db import get_session

logger = structlog.get_logger()

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_admin)],
)

_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(loader=FileSystemLoader(_templates_dir), cache_size=0)
templates = Jinja2Templates(env=_jinja_env)


async def get_sources(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text("SELECT DISTINCT source FROM entities WHERE is_active = TRUE ORDER BY source")
    )
    return [row[0] for row in result.fetchall()]


async def get_place_types(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text("SELECT DISTINCT place_type FROM entities WHERE is_active = TRUE ORDER BY place_type")
    )
    return [row[0] for row in result.fetchall()]


async def get_dashboard_stats(session: AsyncSession) -> dict:
    ent_total = await session.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE"))
    total_entities = ent_total.scalar() or 0

    media_total = await session.execute(text("SELECT COUNT(*) FROM media WHERE is_active = TRUE"))
    total_media = media_total.scalar() or 0

    classif_total = await session.execute(
        text("SELECT COUNT(*) FROM classifications WHERE is_active = TRUE")
    )
    total_classifications = classif_total.scalar() or 0

    sources_result = await session.execute(
        text(
            "SELECT source, COUNT(*) as cnt FROM entities WHERE is_active = TRUE GROUP BY source ORDER BY cnt DESC"
        )
    )
    sources = [{"source": r[0], "count": r[1]} for r in sources_result.fetchall()]

    types_result = await session.execute(
        text(
            "SELECT place_type, COUNT(*) as cnt FROM entities WHERE is_active = TRUE GROUP BY place_type ORDER BY cnt DESC"
        )
    )
    place_types = [{"place_type": r[0], "count": r[1]} for r in types_result.fetchall()]

    return {
        "total_entities": total_entities,
        "total_media": total_media,
        "total_classifications": total_classifications,
        "sources": sources,
        "place_types": place_types,
    }


async def get_unified_stats(session: AsyncSession) -> dict:
    cat_total = await session.execute(
        text("SELECT COUNT(*) FROM unified_categories WHERE is_active = TRUE")
    )
    total_categories = cat_total.scalar() or 0

    map_total = await session.execute(text("SELECT COUNT(*) FROM place_type_mappings"))
    total_mappings = map_total.scalar() or 0

    ent_total = await session.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE"))
    total_entities = ent_total.scalar() or 0

    unified_count = await session.execute(
        text(
            "SELECT COUNT(*) FROM entities WHERE unified_category IS NOT NULL AND is_active = TRUE"
        )
    )
    unified_entities = unified_count.scalar() or 0

    pct_unified = round(unified_entities / total_entities * 100, 1) if total_entities else 0

    low_conf = await session.execute(
        text("SELECT COUNT(*) FROM place_type_mappings WHERE confidence < 100")
    )
    low_confidence_mappings = low_conf.scalar() or 0

    by_cat = await session.execute(
        text(
            "SELECT unified_category, COUNT(*) as cnt FROM entities "
            "WHERE unified_category IS NOT NULL AND is_active = TRUE "
            "GROUP BY unified_category ORDER BY cnt DESC LIMIT 10"
        )
    )
    entities_by_category = [{"category": r[0], "count": r[1]} for r in by_cat.fetchall()]

    unmapped = await session.execute(
        text(
            "SELECT e.source, e.place_type, COUNT(*) as cnt FROM entities e "
            "WHERE e.unified_category IS NULL AND e.is_active = TRUE "
            "GROUP BY e.source, e.place_type ORDER BY cnt DESC LIMIT 10"
        )
    )
    unmapped_types = [
        {"source": r[0], "place_type": r[1], "count": r[2]} for r in unmapped.fetchall()
    ]

    return {
        "total_categories": total_categories,
        "total_mappings": total_mappings,
        "total_entities": total_entities,
        "unified_entities": unified_entities,
        "pct_unified": pct_unified,
        "low_confidence_mappings": low_confidence_mappings,
        "entities_by_category": entities_by_category,
        "unmapped_types": unmapped_types,
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    stats = await get_dashboard_stats(session)
    return templates.TemplateResponse(
        request, "dashboard.html", {"active": "dashboard", "stats": stats}
    )


@router.get("/entities", response_class=HTMLResponse)
async def admin_entities(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: str = "",
    source: str = "",
    place_type: str = "",
    country: str = "",
    page_size: int = 50,
    cursor: str | None = None,
):
    conditions = ["e.is_active = TRUE"]
    params: dict[str, str | int] = {}
    if q:
        conditions.append("e.name ILIKE :q OR e.summary ILIKE :q2")
        params["q"] = f"%{q}%"
        params["q2"] = f"%{q}%"
    if source:
        conditions.append("e.source = :source")
        params["source"] = source
    if place_type:
        conditions.append("e.place_type = :place_type")
        params["place_type"] = place_type
    if country:
        conditions.append("e.country = :country")
        params["country"] = country

    where = " AND ".join(conditions)

    count_sql = text(f"SELECT COUNT(*) FROM entities e WHERE {where}")
    result = await session.execute(count_sql, params)
    total = result.scalar() or 0

    if cursor:
        try:
            cursor_data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_id = cursor_data["id"]
            cursor_sort = cursor_data.get("sort", "")
            conditions.append(
                "(e.name > :cursor_sort OR (e.name = :cursor_sort AND e.id > :cursor_id))"
            )
            params["cursor_id"] = cursor_id
            params["cursor_sort"] = cursor_sort
        except Exception:
            pass

    where2 = " AND ".join(conditions)
    sql = text(
        f"SELECT e.id, e.name, e.source, e.source_id, e.place_type, e.country, e.rating "
        f"FROM entities e WHERE {where2} ORDER BY e.name LIMIT :limit"
    )
    params["limit"] = page_size + 1
    result = await session.execute(sql, params)
    rows = result.fetchall()

    has_more = len(rows) > page_size
    entities = [
        {
            "id": str(r[0]),
            "name": r[1],
            "source": r[2],
            "source_id": r[3],
            "place_type": r[4],
            "country": r[5],
            "rating": float(r[6]) if r[6] else None,
        }
        for r in rows[:page_size]
    ]

    next_cursor = None
    if has_more and entities:
        last = entities[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps({"id": last["id"], "sort": last["name"]}).encode()
        ).decode()

    sources = await get_sources(session)
    place_types = await get_place_types(session)

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request,
            "entities/_list.html",
            {
                "entities": entities,
                "total": total,
                "page_size": page_size,
                "cursor": next_cursor,
            },
        )

    return templates.TemplateResponse(
        request,
        "entities/browse.html",
        {
            "active": "entities",
            "entities": entities,
            "sources": sources,
            "place_types": place_types,
            "q": q,
            "source": source,
            "place_type": place_type,
            "country": country,
            "total": total,
            "page_size": page_size,
            "cursor": next_cursor,
        },
    )


@router.get("/entities/{entity_id}", response_class=HTMLResponse)
async def admin_entity_detail(
    request: Request,
    entity_id: str,
    session: AsyncSession = Depends(get_session),
):
    sql = text("SELECT * FROM entities WHERE id = :id AND is_active = TRUE")
    result = await session.execute(sql, {"id": entity_id})
    row = result.fetchone()
    if not row:
        return HTMLResponse("Entity not found", status_code=404)

    columns = [desc[0] for desc in result.cursor.description]
    entity = dict(zip(columns, row))

    # Convert UUID to string for template
    if "id" in entity and entity["id"]:
        entity["id"] = str(entity["id"])

    # Get media
    media_result = await session.execute(
        text("SELECT * FROM media WHERE entity_id = :eid AND is_active = TRUE ORDER BY sort_order"),
        {"eid": entity_id},
    )
    media_cols = [desc[0] for desc in media_result.cursor.description]
    media = [dict(zip(media_cols, r)) for r in media_result.fetchall()]
    for m in media:
        if "entity_id" in m and m["entity_id"]:
            m["entity_id"] = str(m["entity_id"])

    # Get classifications
    classif_result = await session.execute(
        text("SELECT * FROM classifications WHERE entity_id = :eid AND is_active = TRUE"),
        {"eid": entity_id},
    )
    classif_cols = [desc[0] for desc in classif_result.cursor.description]
    classifications = [dict(zip(classif_cols, r)) for r in classif_result.fetchall()]

    # Get routes
    route_result = await session.execute(
        text("SELECT * FROM routes WHERE entity_id = :eid"),
        {"eid": entity_id},
    )
    route_cols = [desc[0] for desc in route_result.cursor.description]
    routes = [dict(zip(route_cols, r)) for r in route_result.fetchall()]
    for r in routes:
        if "entity_id" in r and r["entity_id"]:
            r["entity_id"] = str(r["entity_id"])

    return templates.TemplateResponse(
        request,
        "entities/detail.html",
        {
            "active": "entities",
            "entity": entity,
            "media": media,
            "classifications": classifications,
            "routes": routes,
        },
    )


@router.get("/classifications", response_class=HTMLResponse)
async def admin_classifications(
    request: Request,
    session: AsyncSession = Depends(get_session),
    category: str = "",
    value_code: str = "",
    page_size: int = 50,
    cursor: str | None = None,
):
    conditions = ["c.is_active = TRUE"]
    params: dict = {}
    if category:
        conditions.append("c.category = :category")
        params["category"] = category
    if value_code:
        conditions.append("c.value_code ILIKE :vc")
        params["vc"] = f"%{value_code}%"

    where = " AND ".join(conditions)
    count_sql = text(f"SELECT COUNT(*) FROM classifications c WHERE {where}")
    result = await session.execute(count_sql, params)
    total = result.scalar() or 0

    sql = text(
        f"SELECT c.id, c.entity_id, c.category, c.value_code, c.value_title "
        f"FROM classifications c WHERE {where} ORDER BY c.category, c.value_code LIMIT :limit"
    )
    params["limit"] = page_size + 1
    result = await session.execute(sql, params)
    rows = result.fetchall()

    classifications = [
        {
            "id": r[0],
            "entity_id": str(r[1]),
            "category": r[2],
            "value_code": r[3],
            "value_title": r[4],
        }
        for r in rows[:page_size]
    ]

    cats_sql = text(
        "SELECT DISTINCT category FROM classifications WHERE is_active = TRUE ORDER BY category"
    )
    cats_result = await session.execute(cats_sql)
    categories = [r[0] for r in cats_result.fetchall()]

    next_cursor = None

    return templates.TemplateResponse(
        request,
        "classifications/browse.html",
        {
            "active": "classifications",
            "classifications": classifications,
            "categories": categories,
            "category": category,
            "value_code": value_code,
            "total": total,
            "page_size": page_size,
            "cursor": next_cursor,
        },
    )


@router.get("/scripts", response_class=HTMLResponse)
async def admin_scripts(request: Request):
    scripts = list_scripts()
    return templates.TemplateResponse(
        request,
        "scripts/list.html",
        {"active": "scripts", "scripts": scripts},
    )


@router.get("/scripts/{script_name}", response_class=HTMLResponse)
async def admin_script_detail(request: Request, script_name: str):
    script = get_script(script_name)
    if not script:
        return HTMLResponse("Script not found", status_code=404)
    meta = script.meta
    return templates.TemplateResponse(
        request,
        "scripts/run.html",
        {
            "active": "scripts",
            "script": {
                "name": meta.name,
                "description": meta.description,
                "category": meta.category,
                "parameters": [
                    {
                        "name": p.name,
                        "label": p.label,
                        "type": p.type,
                        "default": p.default,
                        "options": p.options,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in meta.parameters
                ],
            },
        },
    )


@router.post("/scripts/{script_name}/run")
async def admin_script_run(
    request: Request,
    script_name: str,
    session: AsyncSession = Depends(get_session),
):
    script = get_script(script_name)
    if not script:
        return JSONResponse({"error": "Script not found"}, status_code=404)

    form_data = await request.form()
    params = dict(form_data)

    # Convert boolean strings
    for p in script.meta.parameters:
        if p.type == "boolean" and p.name in params:
            params[p.name] = params[p.name] == "true"
        if p.type == "int" and p.name in params:
            try:
                params[p.name] = int(params[p.name])
            except (ValueError, TypeError):
                params[p.name] = p.default

    run_id = await create_run(script_name)

    async def run_task():
        try:
            await update_run(run_id, status="running", progress_pct=0, message="Starting...")
            s = get_script(script_name)
            if not s:
                await update_run(run_id, status="error", error="Script not found")
                return

            admin_settings = await load_settings()
            llm = LLMClient.from_settings(admin_settings)
            progress_cb = make_progress_callback(run_id)

            result = await s.run(params, session, llm=llm, progress_callback=progress_cb)
            await update_run(
                run_id,
                status="done",
                progress_pct=100,
                message=result.message,
                result={
                    "success": result.success,
                    "message": result.message,
                    "affected_count": result.affected_count,
                    "details": result.details[:100],
                    "total_details": len(result.details),
                },
            )
        except Exception as e:
            logger.error("script_run_failed", script=script_name, error=str(e))
            await update_run(run_id, status="error", error=str(e))

    asyncio.create_task(run_task())

    return JSONResponse(
        {
            "run_id": run_id,
            "status": "queued",
            "message": "Script started",
        }
    )


@router.get("/scripts/runs/{run_id}")
async def admin_script_status(run_id: str):
    run = await get_run(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)

    result = None
    if run.result:
        result = run.result
    elif run.error:
        result = {"success": False, "message": run.error, "affected_count": 0, "details": []}

    return JSONResponse(
        {
            "run_id": run.id,
            "script_name": run.script_name,
            "status": run.status,
            "progress_pct": run.progress_pct,
            "message": run.message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "result": result,
        }
    )


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request):
    settings = await load_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"active": "settings", "settings": settings},
    )


@router.post("/settings")
async def admin_settings_save(request: Request):
    form_data = await request.form()
    data = {
        "llm_endpoint": form_data.get("llm_endpoint", ""),
        "llm_api_key": form_data.get("llm_api_key", ""),
        "llm_model": form_data.get("llm_model", "gpt-4o"),
        "llm_max_tokens": int(form_data.get("llm_max_tokens", 1024)),
        "llm_temperature": float(form_data.get("llm_temperature", 0.7)),
    }
    await save_settings(data)
    return HTMLResponse('<div class="toast success">Settings saved successfully</div>')


@router.post("/settings/test-llm")
async def admin_settings_test_llm():
    settings = await load_settings()
    client = LLMClient.from_settings(settings)
    if not client:
        return HTMLResponse('<div class="toast error">LLM endpoint or API key not configured</div>')
    result = await client.test_connection()
    if result.startswith("ERROR"):
        return HTMLResponse(f'<div class="toast error">{result}</div>')
    return HTMLResponse(f'<div class="toast success">Connection OK — Response: {result}</div>')


# Unified data stats page


@router.get("/unified-data", response_class=HTMLResponse)
async def admin_unified_data(request: Request, session: AsyncSession = Depends(get_session)):
    stats = await get_unified_stats(session)
    return templates.TemplateResponse(
        request, "unified_data.html", {"active": "unified_data", "stats": stats}
    )


# Taxonomy management


@router.get("/taxonomy", response_class=HTMLResponse)
async def admin_taxonomy(request: Request, session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text(
            "SELECT uc.id, uc.slug, uc.name, uc.parent_id, uc.icon, uc.sort_order, uc.is_active, "
            "(SELECT COUNT(*) FROM unified_categories WHERE parent_id = uc.id AND is_active = TRUE) AS children_count, "
            "(SELECT COUNT(*) FROM entities WHERE unified_category = uc.slug AND is_active = TRUE) AS entity_count "
            "FROM unified_categories uc WHERE uc.is_active = TRUE ORDER BY uc.sort_order, uc.name"
        )
    )
    cats = []
    for r in rows.fetchall():
        cats.append(
            {
                "id": r[0],
                "slug": r[1],
                "name": r[2],
                "parent_id": r[3],
                "icon": r[4],
                "sort_order": r[5],
                "is_active": r[6],
                "children_count": r[7],
                "entity_count": r[8],
            }
        )

    parents = [c for c in cats if c["parent_id"] is None]
    return templates.TemplateResponse(
        request,
        "taxonomy/browse.html",
        {"active": "taxonomy", "categories": cats, "parents": parents},
    )


@router.post("/taxonomy", response_class=HTMLResponse)
async def admin_taxonomy_create(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    name = form.get("name", "").strip()
    slug = form.get("slug", "").strip()
    icon = form.get("icon", "").strip() or None
    parent_id = form.get("parent_id", "").strip() or None
    sort_order = int(form.get("sort_order", 0) or 0)

    if parent_id:
        parent_id = int(parent_id)

    if not name or not slug:
        return HTMLResponse('<div class="toast error">Name and slug are required</div>')

    stmt = text(
        "INSERT INTO unified_categories (slug, name, parent_id, icon, sort_order) "
        "VALUES (:slug, :name, :parent_id, :icon, :sort_order)"
    )
    try:
        await session.execute(
            stmt,
            {
                "slug": slug,
                "name": name,
                "parent_id": parent_id,
                "icon": icon,
                "sort_order": sort_order,
            },
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        return HTMLResponse(f'<div class="toast error">{e}</div>')

    return HTMLResponse(f'<div class="toast success">Category "{name}" created</div>')


@router.put("/taxonomy/{category_id}", response_class=HTMLResponse)
async def admin_taxonomy_update(
    request: Request, category_id: int, session: AsyncSession = Depends(get_session)
):
    form = await request.form()
    name = form.get("name", "").strip()
    icon = form.get("icon", "").strip() or None
    parent_id = form.get("parent_id", "").strip() or None
    sort_order = int(form.get("sort_order", 0) or 0)

    if parent_id:
        parent_id = int(parent_id)

    if not name:
        return HTMLResponse('<div class="toast error">Name is required</div>')

    stmt = text(
        "UPDATE unified_categories SET name = :name, icon = :icon, parent_id = :parent_id, sort_order = :sort_order "
        "WHERE id = :id AND is_active = TRUE"
    )
    await session.execute(
        stmt,
        {
            "id": category_id,
            "name": name,
            "icon": icon,
            "parent_id": parent_id,
            "sort_order": sort_order,
        },
    )
    await session.commit()
    return HTMLResponse(f'<div class="toast success">Category "{name}" updated</div>')


@router.delete("/taxonomy/{category_id}", response_class=HTMLResponse)
async def admin_taxonomy_delete(
    request: Request, category_id: int, session: AsyncSession = Depends(get_session)
):
    stmt = text("UPDATE unified_categories SET is_active = FALSE WHERE id = :id")
    await session.execute(stmt, {"id": category_id})
    await session.commit()
    return HTMLResponse('<div class="toast success">Category deactivated</div>')


# Mapping management


@router.get("/mappings", response_class=HTMLResponse)
async def admin_mappings(
    request: Request,
    session: AsyncSession = Depends(get_session),
    source: str = "",
    min_confidence: int = 0,
    page_size: int = 50,
    cursor: str | None = None,
):
    conditions = ["1=1"]
    params: dict = {}
    if source:
        conditions.append("ptm.source = :source")
        params["source"] = source
    if min_confidence:
        conditions.append("ptm.confidence >= :min_conf")
        params["min_conf"] = min_confidence

    where = " AND ".join(conditions)

    count_sql = text(f"SELECT COUNT(*) FROM place_type_mappings ptm WHERE {where}")
    result = await session.execute(count_sql, params)
    total = result.scalar() or 0

    if cursor:
        try:
            cursor_data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_id = cursor_data["id"]
            conditions.append("ptm.id > :cursor_id")
            params["cursor_id"] = cursor_id
        except Exception:
            pass

    where2 = " AND ".join(conditions)
    sql = text(
        f"SELECT ptm.id, ptm.source, ptm.source_place_type, ptm.unified_category_id, "
        f"ptm.confidence, ptm.is_manual, ptm.notes, "
        f"uc.slug AS unified_slug, uc.name AS unified_name "
        f"FROM place_type_mappings ptm "
        f"LEFT JOIN unified_categories uc ON uc.id = ptm.unified_category_id "
        f"WHERE {where2} ORDER BY ptm.id LIMIT :limit"
    )
    params["limit"] = page_size + 1
    result = await session.execute(sql, params)
    rows = result.fetchall()

    has_more = len(rows) > page_size
    mappings = []
    for r in rows[:page_size]:
        mappings.append(
            {
                "id": r[0],
                "source": r[1],
                "source_place_type": r[2],
                "unified_category_id": r[3],
                "confidence": r[4],
                "is_manual": r[5],
                "notes": r[6],
                "unified_slug": r[7],
                "unified_name": r[8],
            }
        )

    next_cursor = None
    if has_more and mappings:
        last = mappings[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"id": last["id"]}).encode()).decode()

    sources = await get_sources(session)
    all_cats_result = await session.execute(
        text("SELECT id, slug, name FROM unified_categories WHERE is_active = TRUE ORDER BY name")
    )
    all_cats = [{"id": r[0], "slug": r[1], "name": r[2]} for r in all_cats_result.fetchall()]

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request,
            "mappings/_list.html",
            {"mappings": mappings, "total": total, "page_size": page_size, "cursor": next_cursor},
        )

    return templates.TemplateResponse(
        request,
        "mappings/browse.html",
        {
            "active": "mappings",
            "mappings": mappings,
            "total": total,
            "page_size": page_size,
            "cursor": next_cursor,
            "sources": sources,
            "source": source,
            "min_confidence": min_confidence,
            "all_categories": all_cats,
        },
    )


@router.post("/mappings", response_class=HTMLResponse)
async def admin_mappings_create(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    source = form.get("source", "").strip()
    source_place_type = form.get("source_place_type", "").strip()
    unified_category_id = form.get("unified_category_id", "").strip() or None
    confidence = int(form.get("confidence", 100) or 100)
    is_manual = form.get("is_manual", "false") == "true"
    notes = form.get("notes", "").strip() or None

    if not source or not source_place_type:
        return HTMLResponse('<div class="toast error">Source and place type are required</div>')

    if unified_category_id:
        unified_category_id = int(unified_category_id)

    stmt = text(
        "INSERT INTO place_type_mappings (source, source_place_type, unified_category_id, confidence, is_manual, notes) "
        "VALUES (:source, :place_type, :cat_id, :conf, :manual, :notes) "
        "ON CONFLICT (source, source_place_type) DO UPDATE SET "
        "unified_category_id = EXCLUDED.unified_category_id, "
        "confidence = EXCLUDED.confidence, is_manual = EXCLUDED.is_manual, notes = EXCLUDED.notes"
    )
    try:
        await session.execute(
            stmt,
            {
                "source": source,
                "place_type": source_place_type,
                "cat_id": unified_category_id,
                "conf": confidence,
                "manual": is_manual,
                "notes": notes,
            },
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        return HTMLResponse(f'<div class="toast error">{e}</div>')

    return HTMLResponse(
        f'<div class="toast success">Mapping for "{source}/{source_place_type}" saved</div>'
    )


@router.put("/mappings/{mapping_id}", response_class=HTMLResponse)
async def admin_mappings_update(
    request: Request, mapping_id: int, session: AsyncSession = Depends(get_session)
):
    form = await request.form()
    unified_category_id = form.get("unified_category_id", "").strip() or None
    confidence = int(form.get("confidence", 100) or 100)
    is_manual = form.get("is_manual", "false") == "true"
    notes = form.get("notes", "").strip() or None

    if unified_category_id:
        unified_category_id = int(unified_category_id)

    stmt = text(
        "UPDATE place_type_mappings SET unified_category_id = :cat_id, "
        "confidence = :conf, is_manual = :manual, notes = :notes WHERE id = :id"
    )
    await session.execute(
        stmt,
        {
            "id": mapping_id,
            "cat_id": unified_category_id,
            "conf": confidence,
            "manual": is_manual,
            "notes": notes,
        },
    )
    await session.commit()
    return HTMLResponse('<div class="toast success">Mapping updated</div>')


@router.delete("/mappings/{mapping_id}", response_class=HTMLResponse)
async def admin_mappings_delete(
    request: Request, mapping_id: int, session: AsyncSession = Depends(get_session)
):
    row = await session.execute(
        text("SELECT source FROM place_type_mappings WHERE id = :id"), {"id": mapping_id}
    )
    source = row.scalar()
    await session.execute(
        text("DELETE FROM place_type_mappings WHERE id = :id"), {"id": mapping_id}
    )
    await session.commit()
    return HTMLResponse(
        f'<div class="toast success">Mapping deleted. '
        f'<a href="/admin/scripts/unify_place_types">Re-run unification for {source}</a></div>'
    )
