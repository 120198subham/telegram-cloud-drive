import os
import time
import json
import logging
import psutil
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("telegram_cloud.telemetry")
LOG_FILE = Path(__file__).parent / "uploads_telemetry.log"

class TelemetryRecorder:
    def __init__(self):
        self.active_sessions: Dict[str, Dict[str, Any]] = {}

    def _get_ram_mb(self) -> float:
        try:
            return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
        except Exception:
            return 0.0

    def _get_cpu_percent(self) -> float:
        try:
            return round(psutil.cpu_percent(interval=None), 1)
        except Exception:
            return 0.0

    def _log_entry(self, entry: Dict[str, Any]):
        entry["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry["epoch"] = time.time()
        entry["ram_mb"] = self._get_ram_mb()
        entry["cpu_pct"] = self._get_cpu_percent()
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write telemetry log: {e}")

    def start_session(self, upload_id: str, filename: str, total_bytes: int):
        now = time.time()
        self.active_sessions[upload_id] = {
            "upload_id": upload_id,
            "filename": filename,
            "total_bytes": total_bytes,
            "start_time": now,
            "stage1_start": now,
            "stage1_end": None,
            "chunks_encrypted": [],
            "chunks_uploaded": [],
            "end_time": None
        }
        self._log_entry({
            "event": "SESSION_STARTED",
            "upload_id": upload_id,
            "filename": filename,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2)
        })

    def complete_stage1(self, upload_id: str, total_received: int):
        now = time.time()
        session = self.active_sessions.get(upload_id)
        duration = round(now - session["stage1_start"], 3) if session else 0.0
        avg_speed_mbs = round((total_received / (1024 * 1024)) / max(duration, 0.001), 2)
        if session:
            session["stage1_end"] = now
            session["stage1_duration"] = duration
            session["stage1_avg_speed"] = avg_speed_mbs

        self._log_entry({
            "event": "STAGE_1_DEVICE_TRANSFER_COMPLETE",
            "upload_id": upload_id,
            "bytes_received": total_received,
            "duration_sec": duration,
            "avg_speed_mbs": avg_speed_mbs
        })

    def record_encryption_chunk(self, upload_id: str, part_idx: int, size_bytes: int, duration_sec: float):
        speed_mbs = round((size_bytes / (1024 * 1024)) / max(duration_sec, 0.001), 2)
        session = self.active_sessions.get(upload_id)
        if session:
            session["chunks_encrypted"].append({
                "part": part_idx,
                "bytes": size_bytes,
                "duration": duration_sec,
                "speed_mbs": speed_mbs
            })
        self._log_entry({
            "event": "STAGE_2_CHUNK_ENCRYPTED",
            "upload_id": upload_id,
            "part": part_idx,
            "bytes": size_bytes,
            "duration_sec": round(duration_sec, 3),
            "speed_mbs": speed_mbs
        })

    def record_telegram_upload(self, upload_id: str, part_idx: int, size_bytes: int, duration_sec: float, msg_id: int):
        speed_mbs = round((size_bytes / (1024 * 1024)) / max(duration_sec, 0.001), 2)
        speed_mbps = round(speed_mbs * 8, 2)
        session = self.active_sessions.get(upload_id)
        if session:
            session["chunks_uploaded"].append({
                "part": part_idx,
                "bytes": size_bytes,
                "duration": duration_sec,
                "speed_mbs": speed_mbs,
                "msg_id": msg_id
            })
        self._log_entry({
            "event": "STAGE_3_CHUNK_UPLOADED_TO_TELEGRAM",
            "upload_id": upload_id,
            "part": part_idx,
            "bytes": size_bytes,
            "duration_sec": round(duration_sec, 3),
            "speed_mbs": speed_mbs,
            "speed_mbps": speed_mbps,
            "msg_id": msg_id
        })

    def complete_session(self, upload_id: str) -> Dict[str, Any]:
        now = time.time()
        session = self.active_sessions.get(upload_id)
        total_duration = round(now - session["start_time"], 3) if session else 0.0
        if session:
            session["end_time"] = now
            session["total_duration"] = total_duration

        self._log_entry({
            "event": "SESSION_COMPLETED",
            "upload_id": upload_id,
            "total_duration_sec": total_duration
        })
        return self.analyze_gaps(upload_id)

    def analyze_gaps(self, upload_id: str) -> Dict[str, Any]:
        session = self.active_sessions.get(upload_id, {})
        total_time = session.get("total_duration", 0.0)
        s1_time = session.get("stage1_duration", 0.0)
        
        enc_chunks = session.get("chunks_encrypted", [])
        total_enc_time = sum(c["duration"] for c in enc_chunks)
        
        up_chunks = session.get("chunks_uploaded", [])
        total_up_time = sum(c["duration"] for c in up_chunks)
        
        # Idle / Handshake gap time
        gap_time = max(0.0, round(total_time - (s1_time + total_up_time), 3))
        
        return {
            "filename": session.get("filename"),
            "total_mb": round(session.get("total_bytes", 0) / (1024 * 1024), 2),
            "total_duration_seconds": total_time,
            "stage_1_device_to_server_seconds": s1_time,
            "stage_2_encryption_seconds": round(total_enc_time, 3),
            "stage_3_telegram_upload_seconds": round(total_up_time, 3),
            "unaccounted_gap_or_handshake_seconds": gap_time,
            "average_telegram_mbps": round((session.get("total_bytes", 0) * 8 / (1024 * 1024)) / max(total_up_time, 0.001), 2) if total_up_time > 0 else 0
        }

    # ==================== DOWNLOAD TELEMETRY TRACKING ====================

    def start_download_session(self, download_id: str, filename: str, total_bytes: int):
        now = time.time()
        self.active_sessions[download_id] = {
            "download_id": download_id,
            "filename": filename,
            "total_bytes": total_bytes,
            "start_time": now,
            "ttfb": None,
            "chunks_downloaded": [],
            "end_time": None
        }
        self._log_entry({
            "event": "DOWNLOAD_SESSION_STARTED",
            "download_id": download_id,
            "filename": filename,
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2)
        })

    def record_download_ttfb(self, download_id: str, ttfb_sec: float):
        session = self.active_sessions.get(download_id)
        if session:
            session["ttfb"] = ttfb_sec
        self._log_entry({
            "event": "DOWNLOAD_TTFB",
            "download_id": download_id,
            "ttfb_seconds": round(ttfb_sec, 3)
        })

    def record_download_chunk(self, download_id: str, part_idx: int, size_bytes: int, duration_sec: float):
        speed_mbs = round((size_bytes / (1024 * 1024)) / max(duration_sec, 0.001), 2)
        speed_mbps = round(speed_mbs * 8, 2)
        session = self.active_sessions.get(download_id)
        if session:
            session["chunks_downloaded"].append({
                "part": part_idx,
                "bytes": size_bytes,
                "duration": duration_sec,
                "speed_mbs": speed_mbs
            })
        self._log_entry({
            "event": "DOWNLOAD_CHUNK_COMPLETED",
            "download_id": download_id,
            "part": part_idx,
            "bytes": size_bytes,
            "duration_sec": round(duration_sec, 3),
            "speed_mbs": speed_mbs,
            "speed_mbps": speed_mbps
        })

    def complete_download_session(self, download_id: str) -> Dict[str, Any]:
        now = time.time()
        session = self.active_sessions.get(download_id)
        total_duration = round(now - session["start_time"], 3) if session else 0.0
        if session:
            session["end_time"] = now
            session["total_duration"] = total_duration

        total_bytes = session.get("total_bytes", 0) if session else 0
        avg_speed_mbs = round((total_bytes / (1024 * 1024)) / max(total_duration, 0.001), 2)
        avg_speed_mbps = round(avg_speed_mbs * 8, 2)

        self._log_entry({
            "event": "DOWNLOAD_SESSION_COMPLETED",
            "download_id": download_id,
            "total_duration_sec": total_duration,
            "avg_speed_mbs": avg_speed_mbs,
            "avg_speed_mbps": avg_speed_mbps
        })
        return {
            "download_id": download_id,
            "filename": session.get("filename") if session else "",
            "total_duration_seconds": total_duration,
            "ttfb_seconds": session.get("ttfb") if session else 0.0,
            "avg_speed_mbps": avg_speed_mbps
        }

recorder = TelemetryRecorder()
