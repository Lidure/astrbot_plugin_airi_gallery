"""Authenticated, snapshot-bound conflict review for Git-synced gallery images."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .gallery_safety import git_blob_sha, is_remote_gallery_image_path, resolve_gallery_local_path
    from .gallery_thumbnail import make_gallery_thumbnail_bytes
except ImportError:
    from gallery_safety import git_blob_sha, is_remote_gallery_image_path, resolve_gallery_local_path
    from gallery_thumbnail import make_gallery_thumbnail_bytes


class GallerySyncConflictManager:
    """List and resolve same-path content conflicts without blind overwrites.

    Tickets are bound to the authenticated Dashboard username and to both
    sides' Git/blob snapshots. A ticket is short-lived and single-use.
    """

    TICKET_TTL_SECONDS = 300
    MAX_CONFLICTS = 100

    def __init__(
        self,
        store,
        sync,
        remote,
        *,
        image_suffixes,
        backup_dir: Path,
        logger=None,
    ) -> None:
        self.store = store
        self.sync = sync
        self.remote = remote
        self.image_suffixes = {str(value).lower() for value in (image_suffixes or ())}
        self.backup_dir = Path(backup_dir)
        self.history_path = self.backup_dir.parent / "sync_conflict_history.json"
        self.logger = logger
        self._tickets: dict[str, dict[str, Any]] = {}
        self._ticket_lock = threading.RLock()
        self._history_lock = threading.RLock()
        self._history = self._load_history()

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            if not isinstance(entries, list):
                return []
            return [entry for entry in entries if isinstance(entry, dict)][-500:]
        except FileNotFoundError:
            return []
        except Exception as exc:
            self._warning(f"[Git Sync] 读取冲突处理历史失败: {type(exc).__name__}")
            return []

    def _save_history_locked(self) -> bool:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": self._history[-500:],
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=".sync-conflict-history-", suffix=".tmp", dir=str(self.history_path.parent)
        )
        try:
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.history_path)
            try:
                os.chmod(self.history_path, 0o600)
            except OSError:
                pass
            return True
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _record_history(self, actor: str, ticket: dict, action: str) -> bool:
        entry = {
            "actor": str(actor),
            "path": str(ticket.get("path", "")),
            "action": str(action),
            "status": "skipped" if action == "skip" else "resolved",
            "local_sha256": str(ticket.get("local_sha256", "")),
            "local_blob_sha": str(ticket.get("local_git_sha", "")),
            "remote_blob_sha": str(ticket.get("remote_git_sha", "")),
            "branch": str(ticket.get("branch", "")),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            with self._history_lock:
                key = (
                    entry["actor"], entry["path"], entry["local_blob_sha"],
                    entry["remote_blob_sha"],
                )
                self._history = [
                    old for old in self._history
                    if (
                        str(old.get("actor", "")), str(old.get("path", "")),
                        str(old.get("local_blob_sha", "")), str(old.get("remote_blob_sha", "")),
                    ) != key
                ]
                self._history.append(entry)
                self._history = self._history[-500:]
                return self._save_history_locked()
        except Exception as exc:
            self._warning(f"[Git Sync] 保存冲突处理历史失败: {type(exc).__name__}")
            return False

    def _history_status_for(self, actor: str, path: str, local_sha: str, remote_sha: str):
        with self._history_lock:
            for entry in reversed(self._history):
                if (
                    str(entry.get("actor", "")) == actor
                    and str(entry.get("path", "")) == path
                    and str(entry.get("local_blob_sha", "")) == local_sha
                    and str(entry.get("remote_blob_sha", "")) == remote_sha
                ):
                    return str(entry.get("status", "")), str(entry.get("created_at", ""))
        return "", ""

    def list_history(self, actor: str, limit: int = 100) -> dict:
        if not isinstance(actor, str) or not actor.strip():
            return {"ok": False, "status": 403, "error": "请先登录 AstrBot WebUI。"}
        try:
            bounded_limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            bounded_limit = 100
        with self._history_lock:
            entries = [
                {
                    "path": str(entry.get("path", "")),
                    "action": str(entry.get("action", "")),
                    "status": str(entry.get("status", "")),
                    "local_sha256": str(entry.get("local_sha256", "")),
                    "local_blob_sha": str(entry.get("local_blob_sha", "")),
                    "remote_blob_sha": str(entry.get("remote_blob_sha", "")),
                    "branch": str(entry.get("branch", "")),
                    "created_at": str(entry.get("created_at", "")),
                }
                for entry in reversed(self._history)
                if str(entry.get("actor", "")) == actor
            ][:bounded_limit]
        return {"ok": True, "count": len(entries), "history": entries}

    def _warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    @staticmethod
    def _now_iso(timestamp: float | None) -> str | None:
        if timestamp is None:
            return None
        try:
            return datetime.fromtimestamp(float(timestamp), timezone.utc).astimezone().isoformat(timespec="seconds")
        except (OverflowError, OSError, TypeError, ValueError):
            return None

    def _safe_local_path(self, git_path: str) -> Path | None:
        path = PurePosixPath(str(git_path))
        if (
            len(path.parts) != 3
            or not is_remote_gallery_image_path(path.as_posix(), self.image_suffixes)
        ):
            return None
        root = Path(self.store.gallery_root.parent)
        candidate = root
        try:
            for part in path.parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    return None
            return resolve_gallery_local_path(root, path.as_posix())
        except (OSError, RuntimeError, ValueError):
            return None

    def _remote_tree_map(self) -> dict[str, dict] | None:
        tree = self.remote.list_tree()
        if tree is None or bool(getattr(self.remote, "last_tree_truncated", False)):
            return None
        result: dict[str, dict] = {}
        for entry in tree:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", ""))
            if (
                len(PurePosixPath(path).parts) == 3
                and (not entry.get("type") or entry.get("type") == "blob")
                and is_remote_gallery_image_path(path, self.image_suffixes)
                and str(entry.get("sha", "")).strip()
            ):
                result[path] = entry
        return result

    def _check_ready(self) -> dict | None:
        if not bool(self.sync.git_sync_enabled):
            return {"ok": False, "status": 409, "error": "Git 远程同步未启用。"}
        if str(self.remote.platform()).lower() != "github":
            return {
                "ok": False,
                "status": 409,
                "error": "冲突管理目前仅支持 GitHub 原子提交；Gitee 暂不开放覆盖操作。",
            }
        return None

    def _new_ticket(self, actor: str, path: str, local_content: bytes, local_stat, remote_entry: dict) -> str:
        token = secrets.token_urlsafe(24)
        now = time.time()
        with self._ticket_lock:
            expired = [
                key for key, value in self._tickets.items()
                if now - float(value.get("created_at", 0)) > self.TICKET_TTL_SECONDS
            ]
            for key in expired:
                self._tickets.pop(key, None)
            self._tickets[token] = {
                "actor": actor,
                "path": path,
                "local_git_sha": git_blob_sha(local_content),
                "local_sha256": hashlib.sha256(local_content).hexdigest(),
                "local_size": len(local_content),
                "local_mtime_ns": int(local_stat.st_mtime_ns),
                "remote_git_sha": str(remote_entry.get("sha", "")).strip(),
                "remote_size": int(remote_entry.get("size", 0) or 0),
                "branch": str(self.remote.branch()),
                "created_at": now,
            }
        return token

    def _get_ticket(self, actor: str, token: str) -> tuple[dict | None, dict | None]:
        if not isinstance(token, str) or not token or len(token) > 128:
            return None, {"ok": False, "status": 400, "error": "冲突确认凭证无效，请刷新清单。"}
        with self._ticket_lock:
            ticket = self._tickets.get(token)
            if ticket is None:
                return None, {"ok": False, "status": 410, "error": "冲突清单已失效，请刷新后重试。"}
            if time.time() - float(ticket.get("created_at", 0)) > self.TICKET_TTL_SECONDS:
                self._tickets.pop(token, None)
                return None, {"ok": False, "status": 410, "error": "冲突清单已过期，请刷新后重试。"}
            if not secrets.compare_digest(
                str(ticket.get("actor", "")).encode("utf-8"),
                str(actor).encode("utf-8"),
            ):
                return None, {"ok": False, "status": 403, "error": "该冲突清单不属于当前登录用户。"}
            return dict(ticket), None

    def _consume_ticket(self, token: str) -> None:
        with self._ticket_lock:
            self._tickets.pop(token, None)

    def _verify_snapshot(
        self, ticket: dict, *, verify_remote_tree: bool = True
    ) -> tuple[Path | None, bytes | None, dict | None, dict | None]:
        path = str(ticket.get("path", ""))
        if str(self.remote.branch()) != str(ticket.get("branch", "")):
            return None, None, None, {
                "ok": False, "status": 409, "error": "Git 同步分支已变化，请刷新冲突清单。"
            }
        local_path = self._safe_local_path(path)
        if local_path is None or not local_path.is_file():
            return None, None, None, {
                "ok": False, "status": 409, "error": "本地文件已不存在或路径不安全，请刷新冲突清单。"
            }
        try:
            local_content = local_path.read_bytes()
        except OSError:
            return None, None, None, {
                "ok": False, "status": 409, "error": "本地文件当前无法读取，未执行任何覆盖。"
            }
        if git_blob_sha(local_content) != str(ticket.get("local_git_sha", "")):
            return None, None, None, {
                "ok": False, "status": 409, "error": "本地图片在预览后发生变化，未执行覆盖；请刷新清单。"
            }
        if verify_remote_tree:
            tree = self._remote_tree_map()
            if tree is None:
                return None, None, None, {
                    "ok": False, "status": 503, "error": "无法完整读取 GitHub 文件树，未执行任何覆盖。"
                }
            entry = tree.get(path)
            if not entry:
                return None, None, None, {
                    "ok": False, "status": 409, "error": "GitHub 远端文件已不存在，请刷新冲突清单。"
                }
            if str(entry.get("sha", "")).strip() != str(ticket.get("remote_git_sha", "")):
                return None, None, None, {
                    "ok": False, "status": 409, "error": "GitHub 图片在预览后发生变化，请刷新清单。"
                }
        else:
            entry = {
                "sha": str(ticket.get("remote_git_sha", "")),
                "size": int(ticket.get("remote_size", 0) or 0),
            }
        return local_path, local_content, entry, None

    def list_conflicts(self, actor: str) -> dict:
        if not isinstance(actor, str) or not actor.strip():
            return {"ok": False, "status": 403, "error": "请先登录 AstrBot WebUI。"}
        ready_error = self._check_ready()
        if ready_error:
            return ready_error
        if not self.sync.sync_lock.acquire(blocking=False):
            return {"ok": False, "status": 409, "busy": True, "error": "当前正在同步或修改图库，请稍后刷新。"}
        try:
            with self.sync.mutation_lock:
                tree = self._remote_tree_map()
                if tree is None:
                    return {"ok": False, "status": 503, "error": "无法完整读取 GitHub 文件树，请检查网络或 API 额度。"}
                with self._ticket_lock:
                    for old_token, old_ticket in list(self._tickets.items()):
                        if old_ticket.get("actor") == actor:
                            self._tickets.pop(old_token, None)
                conflicts = []
                for git_path, remote_entry in sorted(tree.items()):
                    local_path = self._safe_local_path(git_path)
                    if local_path is None or not local_path.is_file():
                        continue
                    try:
                        local_content = local_path.read_bytes()
                        local_stat = local_path.stat()
                    except OSError as exc:
                        conflicts.append({
                            "ticket": "",
                            "path": git_path,
                            "category": PurePosixPath(git_path).parts[1],
                            "name": PurePosixPath(git_path).name,
                            "can_resolve": False,
                            "error": "本地文件无法读取，已禁用覆盖操作。",
                            "local": {"readable": False},
                            "remote": {
                                "blob_sha": str(remote_entry.get("sha", "")),
                                "size": int(remote_entry.get("size", 0) or 0),
                            },
                        })
                        self._warning(f"[Git Sync] WebUI 冲突清单无法读取本地文件 {git_path}: {type(exc).__name__}")
                        continue
                    local_blob_sha = git_blob_sha(local_content)
                    remote_blob_sha = str(remote_entry.get("sha", "")).strip()
                    if local_blob_sha == remote_blob_sha:
                        continue
                    ticket = self._new_ticket(actor, git_path, local_content, local_stat, remote_entry)
                    history_status, history_at = self._history_status_for(
                        actor, git_path, local_blob_sha, remote_blob_sha
                    )
                    conflicts.append({
                        "ticket": ticket,
                        "path": git_path,
                        "category": PurePosixPath(git_path).parts[1],
                        "name": PurePosixPath(git_path).name,
                        "can_resolve": True,
                        "history_status": history_status or None,
                        "history_at": history_at or None,
                        "local": {
                            "sha256": hashlib.sha256(local_content).hexdigest(),
                            "blob_sha": local_blob_sha,
                            "size": len(local_content),
                            "mtime": float(local_stat.st_mtime),
                            "mtime_text": self._now_iso(local_stat.st_mtime),
                        },
                        "remote": {
                            "blob_sha": remote_blob_sha,
                            "size": int(remote_entry.get("size", 0) or 0),
                            "commit_time": None,
                        },
                    })
                    if len(conflicts) >= self.MAX_CONFLICTS:
                        break
                return {
                    "ok": True,
                    "count": len(conflicts),
                    "conflicts": conflicts,
                    "truncated": len(conflicts) >= self.MAX_CONFLICTS,
                    "ticket_ttl_seconds": self.TICKET_TTL_SECONDS,
                    "platform": "github",
                    "branch": str(self.remote.branch()),
                }
        finally:
            self.sync.sync_lock.release()

    def preview(self, actor: str, token: str) -> dict:
        ready_error = self._check_ready()
        if ready_error:
            return ready_error
        ticket, error = self._get_ticket(actor, token)
        if error:
            return error
        # Preview checks the remote bytes against the ticket's Git Blob SHA.
        # That avoids one full recursive tree request for every preview card.
        with self.sync.mutation_lock:
            local_path, local_content, entry, error = self._verify_snapshot(
                ticket, verify_remote_tree=False
            )
            if error:
                self._consume_ticket(token)
                return error

            local_thumbnail = None
            local_preview_error = None
            try:
                local_thumbnail = make_gallery_thumbnail_bytes(local_content)
            except Exception as exc:
                local_preview_error = "本地文件内容无法识别为受支持的图片格式，原文件未修改。"
                self._warning(
                    f"[Git Sync] WebUI 本地冲突预览生成失败 {ticket['path']}: {type(exc).__name__}"
                )

            remote_content = None
            remote_thumbnail = None
            remote_preview_error = None
            try:
                remote_content = self.remote.get_file(ticket["path"])
                if remote_content is None or git_blob_sha(remote_content) != ticket["remote_git_sha"]:
                    remote_preview_error = "无法读取或校验 GitHub 图片；本地预览仍可单独查看。"
                else:
                    try:
                        remote_thumbnail = make_gallery_thumbnail_bytes(remote_content)
                    except Exception as exc:
                        remote_preview_error = "GitHub 文件内容无法识别为受支持的图片格式。"
                        self._warning(
                            f"[Git Sync] WebUI 远端冲突预览生成失败 {ticket['path']}: {type(exc).__name__}"
                        )
            except Exception as exc:
                remote_preview_error = "读取 GitHub 图片失败，请检查网络或稍后重试。"
                self._warning(
                    f"[Git Sync] WebUI 远端冲突预览读取失败 {ticket['path']}: {type(exc).__name__}"
                )

            commit_time = None
            get_commit_time = getattr(self.remote, "get_file_last_commit_time", None)
            if callable(get_commit_time):
                try:
                    commit_time = get_commit_time(ticket["path"])
                except Exception:
                    commit_time = None
            with self._ticket_lock:
                if token in self._tickets:
                    self._tickets[token]["remote_commit_time"] = commit_time

            return {
                "ok": True,
                "path": ticket["path"],
                "local": {
                    "image": local_thumbnail,
                    "content_type": "image/jpeg",
                    "sha256": hashlib.sha256(local_content).hexdigest(),
                    "blob_sha": git_blob_sha(local_content),
                    "size": len(local_content),
                    "mtime_text": self._now_iso(local_path.stat().st_mtime),
                    "preview_error": local_preview_error,
                },
                "remote": {
                    "image": remote_thumbnail,
                    "content_type": "image/jpeg",
                    "sha256": hashlib.sha256(remote_content).hexdigest() if remote_content is not None else "",
                    "blob_sha": git_blob_sha(remote_content) if remote_content is not None else ticket["remote_git_sha"],
                    "size": len(remote_content) if remote_content is not None else int(ticket.get("remote_size", 0)),
                    "commit_time": commit_time,
                    "preview_error": remote_preview_error,
                },
            }

    def _create_backup(self, actor: str, action: str, ticket: dict, local_content: bytes, remote_content: bytes) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self.backup_dir / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + secrets.token_hex(5)
        )
        backup.mkdir(parents=False, exist_ok=False, mode=0o700)
        try:
            os.chmod(backup, 0o700)
        except OSError:
            pass
        suffix = PurePosixPath(ticket["path"]).suffix.lower()[:12]
        local_backup = backup / f"local-before{suffix}"
        remote_backup = backup / f"remote-before{suffix}"
        local_backup.write_bytes(local_content)
        remote_backup.write_bytes(remote_content)
        try:
            os.chmod(local_backup, 0o600)
            os.chmod(remote_backup, 0o600)
        except OSError:
            pass
        manifest = {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "path": ticket["path"],
            "local": {
                "size": len(local_content),
                "sha256": hashlib.sha256(local_content).hexdigest(),
                "git_blob_sha": git_blob_sha(local_content),
            },
            "remote": {
                "size": len(remote_content),
                "sha256": hashlib.sha256(remote_content).hexdigest(),
                "git_blob_sha": git_blob_sha(remote_content),
            },
        }
        manifest_path = backup / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(manifest_path, 0o600)
        except OSError:
            pass
        return backup

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.conflict-", suffix=".tmp", dir=str(path.parent))
        try:
            os.chmod(temp_name, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def resolve(self, actor: str, token: str, action: str, confirmation: str = "") -> dict:
        ticket, error = self._get_ticket(actor, token)
        if error:
            return error
        action = str(action or "").strip()
        if action == "skip":
            history_saved = self._record_history(actor, ticket, "skip")
            self._consume_ticket(token)
            return {
                "ok": True, "action": "skip", "changed": False,
                "history_saved": history_saved,
            }
        if action not in {"keep_local", "keep_remote"}:
            return {"ok": False, "status": 400, "error": "不支持的冲突处理操作。"}
        expected_confirmation = "覆盖远端" if action == "keep_local" else "覆盖本地"
        if not secrets.compare_digest(
            str(confirmation).encode("utf-8"),
            expected_confirmation.encode("utf-8"),
        ):
            return {
                "ok": False,
                "status": 400,
                "error": f"二次确认文本不正确，请输入“{expected_confirmation}”。",
            }
        ready_error = self._check_ready()
        if ready_error:
            return ready_error
        if not self.sync.sync_lock.acquire(blocking=False):
            return {"ok": False, "status": 409, "busy": True, "error": "当前正在同步或修改图库，请稍后重试。"}
        try:
            with self.sync.mutation_lock:
                ticket, error = self._get_ticket(actor, token)
                if error:
                    return error
                local_path, local_content, remote_entry, error = self._verify_snapshot(ticket)
                if error:
                    self._consume_ticket(token)
                    return error
                remote_content = self.remote.get_file(ticket["path"])
                if remote_content is None or git_blob_sha(remote_content) != ticket["remote_git_sha"]:
                    self._consume_ticket(token)
                    return {"ok": False, "status": 409, "error": "远端图片无法读取或 SHA 校验不符，未执行覆盖。"}
                try:
                    if local_path.read_bytes() != local_content:
                        raise OSError("local snapshot changed")
                except OSError:
                    self._consume_ticket(token)
                    return {"ok": False, "status": 409, "error": "本地图片在确认期间再次变化，未执行覆盖；请刷新清单。"}
                latest_tree = self._remote_tree_map()
                latest_entry = latest_tree.get(ticket["path"]) if latest_tree is not None else None
                if (
                    latest_entry is None
                    or str(latest_entry.get("sha", "")).strip() != ticket["remote_git_sha"]
                ):
                    self._consume_ticket(token)
                    return {"ok": False, "status": 409, "error": "GitHub 远端图片在确认期间再次变化，未执行覆盖；请刷新清单。"}
                backup = self._create_backup(actor, action, ticket, local_content, remote_content)
                if action == "keep_local":
                    committed = self.sync.replace_github_conflict(
                        ticket["path"], local_content, ticket["remote_git_sha"]
                    )
                    if not committed:
                        self._consume_ticket(token)
                        return {
                            "ok": False,
                            "status": 409,
                            "error": "GitHub 远端在处理期间发生变化，或原子提交未能确认；双方文件均未被本操作覆盖。请刷新后重新核对。",
                            "backup": str(backup),
                        }
                    self.store.remember_verified_remote_content(
                        ticket["path"], local_content, git_blob_sha(local_content), save=False
                    )
                    self.store.save_hash_index(force=True)
                    warning = "远端已更新；本地哈希索引将在后续同步中重建。" if getattr(self.store, "hash_index_dirty", False) else ""
                else:
                    old_index_entry = None
                    with self.store.hash_index_lock:
                        if ticket["path"] in self.store.hash_index:
                            old_index_entry = dict(self.store.hash_index[ticket["path"]])
                    try:
                        current_path = self._safe_local_path(ticket["path"])
                        if current_path is None or current_path != local_path:
                            raise RuntimeError("本地路径安全检查失败")
                        self._atomic_write(local_path, remote_content)
                        category = PurePosixPath(ticket["path"]).parts[1]
                        self.store.invalidate_category_hash_cache(category)
                        self.store.remember_verified_remote_content(
                            ticket["path"], remote_content, ticket["remote_git_sha"], save=False
                        )
                        self.store.save_hash_index(force=True)
                        if getattr(self.store, "hash_index_dirty", False):
                            raise OSError("哈希索引保存未完成")
                    except Exception as exc:
                        self._atomic_write(local_path, local_content)
                        with self.store.hash_index_lock:
                            if old_index_entry is None:
                                self.store.hash_index.pop(ticket["path"], None)
                            else:
                                self.store.hash_index[ticket["path"]] = old_index_entry
                            self.store.hash_index_dirty = True
                        self.store.save_hash_index(force=True)
                        self._consume_ticket(token)
                        return {
                            "ok": False,
                            "status": 500,
                            "error": f"本地替换失败，已尝试恢复原图：{type(exc).__name__}。",
                            "backup": str(backup),
                        }
                    self.remote.sha_cache[ticket["path"]] = ticket["remote_git_sha"]
                    warning = ""
                history_saved = self._record_history(actor, ticket, action)
                self._consume_ticket(token)
                return {
                    "ok": True,
                    "action": action,
                    "path": ticket["path"],
                    "backup": str(backup),
                    "warning": warning,
                    "changed": True,
                    "history_saved": history_saved,
                }
        finally:
            self.sync.sync_lock.release()
