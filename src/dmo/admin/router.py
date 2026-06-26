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
