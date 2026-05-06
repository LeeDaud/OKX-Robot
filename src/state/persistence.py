"""
状态持久化：原子文件写入 + 崩溃恢复 + 进程锁。
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path("data")
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "bot.lock"


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


# ── 进程锁 ───────────────────────────────────────────────────────

class ProcessLock:
    """单实例锁，防止进程重复启动。"""

    def __init__(self, lock_path: str | Path = LOCK_FILE) -> None:
        self._path = Path(lock_path)
        self._locked = False

    def acquire(self) -> bool:
        _ensure_dir()
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                pid = data.get("pid")
                if pid and self._is_pid_alive(pid):
                    logger.error("Process lock held by PID %d, exiting", pid)
                    return False
                logger.warning("Stale lock file (PID %d), removing", pid or 0)
            except Exception:
                pass
            self._path.unlink(missing_ok=True)

        self._path.write_text(json.dumps({
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }))
        self._locked = True
        logger.info("Process lock acquired: PID %d", os.getpid())
        return True

    def release(self) -> None:
        if self._locked:
            self._path.unlink(missing_ok=True)
            self._locked = False
            logger.info("Process lock released")

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """检查 PID 是否存活（跨平台）。"""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, PermissionError):
            return False

    def __enter__(self):
        success = self.acquire()
        if not success:
            raise RuntimeError("Cannot acquire process lock")
        return self

    def __exit__(self, *args) -> None:
        self.release()


# ── 原子状态持久化 ────────────────────────────────────────────────

class StateManager:
    """原子写入状态文件，用于持久化运行时状态。"""

    def __init__(self, state_path: str | Path = STATE_FILE) -> None:
        self._path = Path(state_path)
        _ensure_dir()

    def load(self) -> dict:
        """加载状态文件，不存在则返回空 dict。"""
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load state file: %s", e)
            return {}

    def save(self, data: dict) -> None:
        """原子写入：写 .tmp 再 rename，防止写入中途崩溃导致损坏。"""
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self._path)
        logger.debug("State saved: %d keys", len(data))

    def update(self, **kwargs) -> None:
        """加载当前状态，更新字段，再写入。"""
        state = self.load()
        state.update(kwargs)
        state["_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save(state)


# ── 策略状态追踪 ─────────────────────────────────────────────────

class StrategyState:
    """记录各策略的最后执行时间和冷却状态。"""

    def __init__(self, state_mgr: StateManager) -> None:
        self._mgr = state_mgr

    def get_last_run(self, strategy: str) -> datetime | None:
        state = self._mgr.load()
        key = f"{strategy}_last_run"
        val = state.get(key)
        if val:
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                return None
        return None

    def set_last_run(self, strategy: str, dt: datetime | None = None) -> None:
        self._mgr.update(**{f"{strategy}_last_run": (dt or datetime.now(timezone.utc)).isoformat()})

    def is_on_cooldown(self, strategy: str, cooldown_seconds: float) -> bool:
        last = self.get_last_run(strategy)
        if last is None:
            return False
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < cooldown_seconds
