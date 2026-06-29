from sqlalchemy import text

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult

# Known German city → state mapping
GERMAN_REGIONS = {
    "berlin": "Berlin",
    "hamburg": "Hamburg",
    "munich": "Bavaria",
    "münchen": "Bavaria",
    "cologne": "North Rhine-Westphalia",
    "köln": "North Rhine-Westphalia",
    "frankfurt": "Hesse",
    "stuttgart": "Baden-Württemberg",
    "düsseldorf": "North Rhine-Westphalia",
    "dortmund": "North Rhine-Westphalia",
    "essen": "North Rhine-Westphalia",
    "leipzig": "Saxony",
    "bremen": "Bremen",
    "dresden": "Saxony",
    "hannover": "Lower Saxony",
    "nuremberg": "Bavaria",
    "nürnberg": "Bavaria",
    "duisburg": "North Rhine-Westphalia",
    "bochum": "North Rhine-Westphalia",
    "bonn": "North Rhine-Westphalia",
    "bielefeld": "North Rhine-Westphalia",
    "mannheim": "Baden-Württemberg",
    "karlsruhe": "Baden-Württemberg",
    "wiesbaden": "Hesse",
    "münster": "North Rhine-Westphalia",
    "augsburg": "Bavaria",
    "aachen": "North Rhine-Westphalia",
    "krefeld": "North Rhine-Westphalia",
    "oberhausen": "North Rhine-Westphalia",
    "hagen": "North Rhine-Westphalia",
    "hamm": "North Rhine-Westphalia",
    "mainz": "Rhineland-Palatinate",
    "saarbrücken": "Saarland",
    "saarbruecken": "Saarland",
    "potsdam": "Brandenburg",
    "kiel": "Schleswig-Holstein",
    "magdeburg": "Saxony-Anhalt",
    "freiburg": "Baden-Württemberg",
    "rostock": "Mecklenburg-Vorpommern",
    "lübeck": "Schleswig-Holstein",
    "erfurt": "Thuringia",
    "heidelberg": "Baden-Württemberg",
    "darmstadt": "Hesse",
    "regensburg": "Bavaria",
    "würzburg": "Bavaria",
    "ingolstadt": "Bavaria",
    "ulm": "Baden-Württemberg",
    "wolfsburg": "Lower Saxony",
    "göttingen": "Lower Saxony",
    "koblenz": "Rhineland-Palatinate",
    "trier": "Rhineland-Palatinate",
    "passau": "Bavaria",
    "konstanz": "Baden-Württemberg",
    "bamberg": "Bavaria",
    "bayreuth": "Bavaria",
    "stralsund": "Mecklenburg-Vorpommern",
    "wismar": "Mecklenburg-Vorpommern",
    "quedlinburg": "Saxony-Anhalt",
    "weimar": "Thuringia",
    "rothenburg": "Bavaria",
    "neumünster": "Schleswig-Holstein",
    "flensburg": "Schleswig-Holstein",
    "cuxhaven": "Lower Saxony",
    "emden": "Lower Saxony",
    "wilhelmshaven": "Lower Saxony",
    "delmenhorst": "Lower Saxony",
    "Oldenburg": "Lower Saxony",
    "osnabrück": "Lower Saxony",
    "osnabrueck": "Lower Saxony",
    "minden": "North Rhine-Westphalia",
    "paderborn": "North Rhine-Westphalia",
    "siegen": "North Rhine-Westphalia",
    "wuppertal": "North Rhine-Westphalia",
    "solingen": "North Rhine-Westphalia",
    "remscheid": "North Rhine-Westphalia",
    "leverkusen": "North Rhine-Westphalia",
    "neuss": "North Rhine-Westphalia",
    "mönchengladbach": "North Rhine-Westphalia",
    "moenchengladbach": "North Rhine-Westphalia",
    "gelsenkirchen": "North Rhine-Westphalia",
    "herne": "North Rhine-Westphalia",
    "bottrop": "North Rhine-Westphalia",
    "bad_homburg": "Hesse",
    "offenbach": "Hesse",
    "hanau": "Hesse",
    "marburg": "Hesse",
    "giessen": "Hesse",
    "fulda": "Hesse",
    "kassel": "Hesse",
}


class CleanDztData(AdminScript):
    meta = ScriptMeta(
        name="clean_dzt_data",
        description=(
            "Clean DZT-specific garbage data: fix country URLs, normalize region"
            " names, remove n.v./?? placeholders."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=1000),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 1000))

        results = {}

        # ── 1. Fix country: extract from URL or default to DE ──
        country_sql = text("""
            UPDATE entities
            SET country = 'DE'
            WHERE source = 'dzt'
              AND is_active = TRUE
              AND country LIKE 'http%'
        """)
        count = await self._exec_update(db, country_sql, dry_run)
        results["country_fixed"] = count

        # ── 2. Clean region: remove n.v./??/empty, normalize known cities ──
        region_sql = text("""
            UPDATE entities
            SET region = NULL
            WHERE source = 'dzt'
              AND is_active = TRUE
              AND (region IS NULL OR region = '' OR region IN ('n.v.', '??'))
        """)
        count = await self._exec_update(db, region_sql, dry_run)
        results["region_cleared"] = count

        total = sum(results.values())
        action = "Would fix" if dry_run else "Fixed"
        details = [{"step": k, "count": v} for k, v in results.items() if v > 0]
        msg = f"{action} {total} DZT entities"

        if not dry_run:
            # City → state normalization: process in batches via keyset pagination
            locality_sql = text("""
                SELECT e.id, e.locality
                FROM entities e
                WHERE e.source = 'dzt'
                  AND e.is_active = TRUE
                  AND e.locality IS NOT NULL
                  AND e.locality != ''
                  AND e.region IS NULL
                  AND e.id > :last_id
                ORDER BY e.id
                LIMIT :limit
            """)

            update_sql = text("UPDATE entities SET region = :state WHERE id = :id")

            last_id = "00000000-0000-0000-0000-000000000000"
            city_fixed = 0
            while True:
                rows = (
                    await db.execute(locality_sql, {"last_id": last_id, "limit": batch_size})
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    eid, locality = row
                    if not locality:
                        continue
                    state = GERMAN_REGIONS.get(locality.strip().lower())
                    if state:
                        await db.execute(update_sql, {"id": eid, "state": state})
                        city_fixed += 1
                last_id = rows[-1][0]
                await db.commit()
                if progress_callback:
                    progress_callback(f"Region normalization: {city_fixed} entities")
            results["region_from_locality"] = city_fixed
            total += city_fixed
            msg = f"\nFixed {total} DZT entities: {results}"

        return ScriptResult(success=True, message=msg, affected_count=total, details=details)

    async def _exec_update(self, db, sql, dry_run):
        # Accept both string and TextClause
        sql_str = sql.text if hasattr(sql, "text") else str(sql)
        if dry_run:
            count_sql = sql_str.replace("UPDATE entities", "SELECT COUNT(*) FROM entities")
            set_idx = count_sql.find("SET ")
            count_sql = count_sql[:set_idx] + count_sql[count_sql.find(" WHERE ") :]
            result = await db.execute(text(count_sql))
            return result.scalar() or 0
        result = await db.execute(text(sql_str))
        affected = result.rowcount
        await db.commit()
        return affected
