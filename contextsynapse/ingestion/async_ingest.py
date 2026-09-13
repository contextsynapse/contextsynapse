"""Async Content Ingestion — background job processing with sensor wiring.

When content is ingested (YouTube transcript, analyst report, earnings call),
it should:
  1. Return immediately with a job_id (non-blocking)
  2. Process in background (download, transcribe, extract)
  3. Create graph nodes in the correct stock context
  4. Update the relevant sensor readings
  5. Re-run fusion to update the stock's conviction score
  6. Notify the FM when done

Usage:
    manager = AsyncIngestManager(registry)
    job = manager.submit_youtube("https://...", entity="tcs", job_type="earnings_call")
    # Returns immediately: {"job_id": "job_xxx", "status": "queued"}

    # Later:
    status = manager.get_job(job.job_id)
    # {"status": "completed", "facts_extracted": 45, "score_change": "+0.06"}
"""
import logging
import threading
import uuid
import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

JOB_DIR = Path(__file__).parent.parent.parent / ".ingestion_jobs"


@dataclass
class IngestJob:
    """Tracks an async ingestion job."""
    job_id: str = ""
    status: str = "queued"           # queued, processing, extracting, wiring, completed, failed
    entity: str = ""                 # target stock/context (e.g., "tcs")
    source_type: str = ""            # youtube, audio, pdf, analyst_report, earnings_call
    source_url: str = ""
    job_type: str = "general"        # earnings_call, analyst_opinion, conference, management_interview, research
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    # Results
    transcript_length: int = 0
    facts_extracted: int = 0
    entities_extracted: int = 0
    sensor_readings_created: int = 0
    score_before: float = 0.0
    score_after: float = 0.0
    score_change: float = 0.0
    error: str = ""
    # Progress
    progress_pct: int = 0
    progress_message: str = ""

    def __post_init__(self):
        if not self.job_id:
            self.job_id = f"job_{uuid.uuid4().hex[:10]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return asdict(self)

    def save(self):
        """Persist job state to disk."""
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        path = JOB_DIR / f"{self.job_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, job_id: str) -> Optional["IngestJob"]:
        path = JOB_DIR / f"{job_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None


# ── Sensor type mapping: what job types feed into which sensors ──
JOB_TYPE_SENSOR_MAP = {
    "earnings_call":        {"sensor": "fundamentals",   "weight": 0.15, "decay": 0.08, "category": "earnings"},
    "analyst_opinion":      {"sensor": "analyst_action", "weight": 0.12, "decay": 0.10, "category": "analyst"},
    "conference":           {"sensor": "company_news",   "weight": 0.10, "decay": 0.12, "category": "conference"},
    "management_interview": {"sensor": "company_news",   "weight": 0.12, "decay": 0.10, "category": "management"},
    "research":             {"sensor": "analyst_action", "weight": 0.10, "decay": 0.12, "category": "research"},
    "general":              {"sensor": "company_news",   "weight": 0.08, "decay": 0.15, "category": "ingested"},
}


class AsyncIngestManager:
    """Manages async content ingestion with sensor wiring."""

    def __init__(self, registry):
        self._registry = registry

    # ── Submit Jobs ──────────────────────────────────────────────

    def submit_youtube(self, url: str, entity: str, job_type: str = "general",
                       languages: List[str] = None) -> IngestJob:
        """Submit a YouTube video for async transcription + ingestion."""
        job = IngestJob(
            entity=entity.lower().replace(" ", "_").replace(".ns", "").replace(".bo", ""),
            source_type="youtube",
            source_url=url,
            job_type=job_type,
        )
        job.save()
        # Start background processing
        t = threading.Thread(target=self._process_youtube, args=(job.job_id, url, languages), daemon=True)
        t.start()
        logger.info("[ASYNC-INGEST] YouTube job %s queued for %s: %s", job.job_id, entity, url)
        return job

    def submit_text(self, text: str, entity: str, title: str = "",
                    job_type: str = "general") -> IngestJob:
        """Submit raw text (analyst note, pasted transcript) for ingestion."""
        job = IngestJob(
            entity=entity.lower().replace(" ", "_").replace(".ns", "").replace(".bo", ""),
            source_type="text",
            source_url=title or "manual_text",
            job_type=job_type,
        )
        job.save()
        t = threading.Thread(target=self._process_text, args=(job.job_id, text, title), daemon=True)
        t.start()
        return job

    def submit_file(self, file_path: str, entity: str, job_type: str = "general") -> IngestJob:
        """Submit a file (PDF, DOCX) for async extraction + ingestion."""
        job = IngestJob(
            entity=entity.lower().replace(" ", "_").replace(".ns", "").replace(".bo", ""),
            source_type="file",
            source_url=file_path,
            job_type=job_type,
        )
        job.save()
        t = threading.Thread(target=self._process_file, args=(job.job_id, file_path), daemon=True)
        t.start()
        return job

    # ── Query Jobs ───────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[Dict]:
        job = IngestJob.load(job_id)
        return job.to_dict() if job else None

    def list_jobs(self, entity: str = None, status: str = None, limit: int = 20) -> List[Dict]:
        """List recent ingestion jobs."""
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        jobs = []
        for f in sorted(JOB_DIR.glob("job_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                if entity and data.get("entity") != entity:
                    continue
                if status and data.get("status") != status:
                    continue
                jobs.append(data)
                if len(jobs) >= limit:
                    break
            except Exception:
                continue
        return jobs

    # ── Background Processors ────────────────────────────────────

    def _process_youtube(self, job_id: str, url: str, languages: List[str] = None):
        job = IngestJob.load(job_id)
        if not job:
            return

        try:
            # Phase 1: Transcribe
            job.status = "processing"
            job.progress_pct = 10
            job.progress_message = "Downloading and transcribing video..."
            job.started_at = datetime.now(timezone.utc).isoformat()
            job.save()

            from contextsynapse.connectors.financial.youtube import transcribe_youtube
            transcript = transcribe_youtube(url, languages=languages or ["en", "hi"])

            if not transcript:
                job.status = "failed"
                job.error = "Could not extract transcript (no captions, Whisper fallback failed)"
                job.save()
                return

            job.transcript_length = len(transcript)
            job.progress_pct = 40
            job.progress_message = f"Transcript ready ({len(transcript)} chars). Extracting intelligence..."
            job.save()

            # Phase 2: Extract + Ingest
            self._extract_and_wire(job, transcript, title=f"YouTube: {url[:60]}")

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.save()
            logger.exception("[ASYNC-INGEST] YouTube job %s failed", job_id)

    def _process_text(self, job_id: str, text: str, title: str = ""):
        job = IngestJob.load(job_id)
        if not job:
            return
        try:
            job.status = "processing"
            job.started_at = datetime.now(timezone.utc).isoformat()
            job.transcript_length = len(text)
            job.progress_pct = 30
            job.progress_message = "Extracting intelligence from text..."
            job.save()
            self._extract_and_wire(job, text, title=title or "Manual text input")
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.save()

    def _process_file(self, job_id: str, file_path: str):
        job = IngestJob.load(job_id)
        if not job:
            return
        try:
            job.status = "processing"
            job.started_at = datetime.now(timezone.utc).isoformat()
            job.progress_pct = 10
            job.progress_message = "Extracting text from file..."
            job.save()

            # Extract text from file
            text = ""
            try:
                from contextsynapse.extraction.extract_engine import ExtractEngine
                engine = ExtractEngine()
                result = engine.extract(file_path)
                text = result.get("text", "")
            except Exception:
                # Fallback: read as plain text
                with open(file_path, "r", errors="ignore") as f:
                    text = f.read()

            if not text:
                job.status = "failed"
                job.error = "Could not extract text from file"
                job.save()
                return

            job.transcript_length = len(text)
            job.progress_pct = 30
            job.progress_message = f"Text extracted ({len(text)} chars). Processing..."
            job.save()
            self._extract_and_wire(job, text, title=os.path.basename(file_path))
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.save()

    # ── Core: Extract → Ingest → Wire to Sensor ─────────────────

    def _extract_and_wire(self, job: IngestJob, text: str, title: str = ""):
        """The core pipeline: extract facts → create graph nodes → update sensors → re-fuse."""
        entity = job.entity

        # Phase 2: Ingest into entity's graph
        job.status = "extracting"
        job.progress_pct = 50
        job.progress_message = "Creating graph nodes..."
        job.save()

        db = self._registry.get_graph(entity, load_if_missing=True)
        if db is None:
            db = self._registry.create_graph(entity)

        facts_count = 0
        entities_count = 0

        try:
            from contextsynapse.ingestion.smart_ingest import ingest_text
            result = ingest_text(text, db, title=title, context_purpose=f"{entity} analysis")
            facts_count = result.get("facts_created", result.get("nodes_created", 0))
            entities_count = result.get("entities_created", 0)
        except Exception as e:
            logger.warning("[ASYNC-INGEST] smart_ingest failed for %s, using basic ingest: %s", entity, e)
            # Basic fallback: create a single Fact node with the full text
            try:
                node_id = f"ingest_{uuid.uuid4().hex[:8]}"
                from contextsynapse.context.composite import _make_node, _safe_add_node
                props = {
                    "statement": text[:500],
                    "full_text": text,
                    "title": title,
                    "source": job.source_type,
                    "source_url": job.source_url,
                    "job_type": job.job_type,
                    "_created_at": datetime.now(timezone.utc).isoformat(),
                    "_sentiment": "neutral",
                }
                _safe_add_node(db, _make_node(node_id, "Fact", props))
                facts_count = 1
            except Exception:
                pass

        job.facts_extracted = facts_count
        job.entities_extracted = entities_count
        job.progress_pct = 70
        job.progress_message = f"Extracted {facts_count} facts. Wiring to sensors..."
        job.save()

        # Phase 3: Wire to sensor — create a sensor reading from this content
        job.status = "wiring"
        sensor_config = JOB_TYPE_SENSOR_MAP.get(job.job_type, JOB_TYPE_SENSOR_MAP["general"])
        readings_created = 0

        try:
            # Compute sentiment from extracted facts
            sentiment = self._compute_sentiment(db, entity)

            # Create a custom sensor reading for this ingestion
            from plugins.stock_analysis.sensor_fusion import SensorReading
            reading = SensorReading(
                sensor=f"{entity}_{sensor_config['category']}",
                sensor_type="decaying",
                value=sentiment,
                raw_value=sentiment,
                timestamp=datetime.now(timezone.utc).isoformat(),
                label=f"{title[:80]} ({facts_count} facts)",
                evidence=text[:200],
                source_url=job.source_url,
                category=sensor_config["category"],
                decay_rate=sensor_config["decay"],
                weight=sensor_config["weight"],
                relevance_score=1.0,
                confidence=0.8,
                explanation=f"Ingested from {job.source_type} ({job.job_type}). {facts_count} facts extracted.",
            )

            # Save as custom sensor for this entity
            config_dir = Path(__file__).parent.parent.parent / "plugins" / "stock_analysis" / ".signal_config" / "custom_sensors"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / f"{entity}.json"

            existing = []
            if config_path.exists():
                try:
                    existing = json.loads(config_path.read_text())
                except Exception:
                    existing = []

            existing.append({
                "id": f"ingest_{job.job_id}",
                "name": title[:60],
                "type": "decaying",
                "value": sentiment,
                "label": f"{title[:60]} ({facts_count} facts)",
                "evidence": text[:200],
                "category": sensor_config["category"],
                "decay_rate": sensor_config["decay"],
                "weight": sensor_config["weight"],
                "active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": job.source_type,
                "job_id": job.job_id,
            })

            config_path.write_text(json.dumps(existing, indent=2))
            readings_created = 1
            logger.info("[ASYNC-INGEST] Sensor reading created for %s: %s = %.3f", entity, sensor_config["sensor"], sentiment)

        except Exception as e:
            logger.warning("[ASYNC-INGEST] Sensor wiring failed for %s: %s", entity, e)

        job.sensor_readings_created = readings_created
        job.progress_pct = 85
        job.progress_message = "Re-computing fusion score..."
        job.save()

        # Phase 4: Re-run fusion to update score
        try:
            import sys
            _root = str(Path(__file__).parent.parent.parent)
            if _root not in sys.path:
                sys.path.insert(0, _root)
            from plugins.stock_analysis.sensor_fusion import SensorFusionEngine

            engine = SensorFusionEngine(self._registry)

            # Get score before
            job.score_before = 0.0
            try:
                # Clear Redis cache so we get fresh score
                import redis
                r = redis.Redis()
                r.delete(f"fusion:{entity}:7")
            except Exception:
                pass

            result = engine.fuse(entity, days=7)
            job.score_after = result.fused_score
            job.score_change = job.score_after - job.score_before

            logger.info("[ASYNC-INGEST] Fusion re-run for %s: score=%.3f dir=%s",
                        entity, result.fused_score, result.direction)
        except Exception as e:
            logger.warning("[ASYNC-INGEST] Fusion re-run failed for %s: %s", entity, e)

        # Phase 5: Notify FM
        try:
            from verticals.pms.backend.notifications import NotificationService, Notification, NotificationChannel, NotificationPriority
            ns = NotificationService()
            direction = "bullish" if job.score_after > 0.05 else "bearish" if job.score_after < -0.05 else "neutral"
            notif = Notification(
                recipient="all",
                channel=NotificationChannel.UI,
                priority=NotificationPriority.NORMAL,
                title=f"{entity.upper()} — {job.job_type.replace('_', ' ').title()} processed",
                message=(
                    f"Ingested {job.source_type}: {title[:50]}. "
                    f"{facts_count} facts extracted. "
                    f"Score: {job.score_after:+.3f} ({direction}). "
                    f"Change: {job.score_change:+.3f}."
                ),
                action_url=f"/pms/stock/{entity}",
            )
            ns.queue(notif)
        except Exception:
            pass

        # Done
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        job.progress_pct = 100
        job.progress_message = f"Done — {facts_count} facts, score {job.score_after:+.3f}"
        job.save()
        logger.info("[ASYNC-INGEST] Job %s completed: %d facts, score %+.3f → %+.3f",
                    job.job_id, facts_count, job.score_before, job.score_after)

    def _compute_sentiment(self, db, entity: str) -> float:
        """Compute net sentiment from recent Fact nodes in the entity's graph."""
        try:
            from contextsynapse.context.composite import _get_all_nodes
            nodes = _get_all_nodes(db)
            recent_facts = [n for n in nodes if n.label == "Fact"
                           and (n.properties if hasattr(n, "properties") else {}).get("_sentiment")]
            pos = sum(1 for f in recent_facts
                      if (f.properties if hasattr(f, "properties") else {}).get("_sentiment") == "positive")
            neg = sum(1 for f in recent_facts
                      if (f.properties if hasattr(f, "properties") else {}).get("_sentiment") == "negative")
            total = pos + neg
            if total > 0:
                return (pos - neg) / total
        except Exception:
            pass
        return 0.0
