import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db.connection import get_db
from ..workstation.watcher import workstation_watcher

logger = logging.getLogger("vayom.api.workstation")
router = APIRouter(prefix="/api/v1/workstation", tags=["workstation"])


class CreateWatchFolderRequest(BaseModel):
    path: str
    label: Optional[str] = None
    target_album_id: Optional[str] = None
    auto_delete_local: bool = False


class UpdateWatchFolderRequest(BaseModel):
    label: Optional[str] = None
    target_album_id: Optional[str] = None
    auto_delete_local: Optional[bool] = None
    is_active: Optional[bool] = None


@router.get("/watch-folders")
async def list_watch_folders() -> List[Dict[str, Any]]:
    """Lists all configured workstation watch folders with live sync counts."""
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT 
                wf.id, wf.path, wf.label, wf.target_album_id, wf.auto_delete_local, 
                wf.is_active, wf.last_synced_at, wf.created_at,
                COUNT(wfe.id) AS total_events,
                SUM(CASE WHEN wfe.status = 'synced' THEN 1 ELSE 0 END) AS synced_count,
                SUM(CASE WHEN wfe.status = 'syncing' THEN 1 ELSE 0 END) AS syncing_count,
                SUM(CASE WHEN wfe.status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM watch_folders wf
            LEFT JOIN watch_folder_events wfe ON wf.id = wfe.folder_id
            GROUP BY wf.id
            ORDER BY wf.created_at DESC
            """
        )
        rows = await cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["auto_delete_local"] = bool(d.get("auto_delete_local"))
            d["is_active"] = bool(d.get("is_active"))
            d["exists"] = Path(d["path"]).expanduser().is_dir()
            results.append(d)
        return results


@router.post("/watch-folders")
async def create_watch_folder(req: CreateWatchFolderRequest) -> Dict[str, Any]:
    """Registers a new local watch folder for automated media ingestion."""
    resolved_path = Path(req.path).expanduser().resolve()
    if not resolved_path.exists():
        raise HTTPException(status_code=400, detail=f"Directory path does not exist: {req.path}")
    if not resolved_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {req.path}")

    folder_id = f"wf_{uuid.uuid4().hex[:12]}"
    label = req.label.strip() if req.label and req.label.strip() else resolved_path.name or str(resolved_path)

    async with get_db() as db:
        # Check if already registered
        cur = await db.execute("SELECT id FROM watch_folders WHERE path = ?", (str(resolved_path),))
        if await cur.fetchone():
            raise HTTPException(status_code=409, detail="This directory path is already being watched.")

        await db.execute(
            """
            INSERT INTO watch_folders (
                id, path, label, target_album_id, auto_delete_local, is_active
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                folder_id,
                str(resolved_path),
                label,
                req.target_album_id,
                1 if req.auto_delete_local else 0
            )
        )
        await db.commit()

    # Dynamic reload in watcher engine
    await workstation_watcher.reload()
    logger.info(f"Registered watch folder {folder_id} for path {resolved_path}")

    return {
        "id": folder_id,
        "path": str(resolved_path),
        "label": label,
        "target_album_id": req.target_album_id,
        "auto_delete_local": req.auto_delete_local,
        "is_active": True,
        "exists": True
    }


@router.patch("/watch-folders/{folder_id}")
async def update_watch_folder(folder_id: str, req: UpdateWatchFolderRequest) -> Dict[str, Any]:
    """Updates watch folder configuration."""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM watch_folders WHERE id = ?", (folder_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watch folder not found")

        current = dict(row)
        label = req.label if req.label is not None else current["label"]
        target_album_id = req.target_album_id if req.target_album_id is not None else current["target_album_id"]
        auto_delete_local = int(req.auto_delete_local) if req.auto_delete_local is not None else current["auto_delete_local"]
        is_active = int(req.is_active) if req.is_active is not None else current["is_active"]

        await db.execute(
            """
            UPDATE watch_folders
            SET label = ?, target_album_id = ?, auto_delete_local = ?, is_active = ?
            WHERE id = ?
            """,
            (label, target_album_id, auto_delete_local, is_active, folder_id)
        )
        await db.commit()

    await workstation_watcher.reload()
    return {"status": "updated", "id": folder_id}


@router.delete("/watch-folders/{folder_id}")
async def delete_watch_folder(folder_id: str) -> Dict[str, Any]:
    """Removes a watch folder from active monitoring."""
    async with get_db() as db:
        cur = await db.execute("SELECT id FROM watch_folders WHERE id = ?", (folder_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="Watch folder not found")

        await db.execute("DELETE FROM watch_folders WHERE id = ?", (folder_id,))
        await db.commit()

    await workstation_watcher.reload()
    logger.info(f"Deleted watch folder {folder_id}")
    return {"status": "deleted", "id": folder_id}


@router.post("/watch-folders/{folder_id}/scan")
async def scan_watch_folder(folder_id: str) -> Dict[str, Any]:
    """Forces an immediate rescan of all files inside the watch folder."""
    try:
        count = await workstation_watcher.scan_folder(folder_id)
        return {"status": "scan_started", "folder_id": folder_id, "queued_files": count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error rescanning watch folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events")
async def list_watch_events(
    folder_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200)
) -> List[Dict[str, Any]]:
    """Returns recent watch folder sync events."""
    async with get_db() as db:
        if folder_id:
            cur = await db.execute(
                """
                SELECT wfe.*, wf.label AS folder_label, f.filename
                FROM watch_folder_events wfe
                JOIN watch_folders wf ON wfe.folder_id = wf.id
                LEFT JOIN files f ON wfe.file_id = f.id
                WHERE wfe.folder_id = ?
                ORDER BY wfe.created_at DESC
                LIMIT ?
                """,
                (folder_id, limit)
            )
        else:
            cur = await db.execute(
                """
                SELECT wfe.*, wf.label AS folder_label, f.filename
                FROM watch_folder_events wfe
                JOIN watch_folders wf ON wfe.folder_id = wf.id
                LEFT JOIN files f ON wfe.file_id = f.id
                ORDER BY wfe.created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
