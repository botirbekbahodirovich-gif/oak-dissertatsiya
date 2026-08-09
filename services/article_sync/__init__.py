# -*- coding: utf-8 -*-
"""Mavjud maqola metadata sinxronizatsiyasi — diff engine + dashboard
(Phase 2C.3A + 2C.3B).

Ommaviy API:

    check_article_metadata(cur, article_id, actor_id, actor_type='admin', ...)
    apply_article_metadata_changes(cur, article_id, sync_id, actor_id,
                                   selected_fields, confirm_retraction=False,
                                   confirm_doi_mismatch=False)
    dismiss_sync_result(cur, sync_id, actor_id, reason=None)
    bulk_apply_safe_changes(cur, sync_ids, actor_id)
    list_sync_history(cur, article_id, limit=10, offset=0)
    list_dashboard(cur, **filters)
    get_dashboard_summary(cur)

Flask'ga BOG'LIQ EMAS (§31/§36 — kelajakdagi scheduler AYNAN shu
`check_article_metadata`ni `actor_type='system'` bilan chaqira oladi,
parallel logika yaratilmaydi). Metadata FAQAT
`services.article_metadata.fetch_article_metadata` orqali olinadi —
Crossref/OpenAlex/Unpaywall mantig'i bu yerda QAYTA YOZILMAYDI.
"""
from services.article_sync.diff import compare_article_metadata
from services.article_sync.models import (CRITICAL, REVIEW, SAFE, UNCHANGED,
                                          MetadataFieldDiff, MetadataSyncResult)
from services.article_sync.policy import APPLICABLE_FIELDS, FIELD_LABELS
from services.article_sync.scheduler import (SchedulerAlreadyRunningError,
                                             SchedulerRunResult,
                                             list_recent_runs as list_recent_scheduler_runs,
                                             run_scheduled_metadata_sync,
                                             select_due_articles)
from services.article_sync.scheduler_config import (SchedulerConfig,
                                                     SchedulerConfigError,
                                                     scheduler_config_from_env)
from services.article_sync.scheduler_state import (RUN_COMPLETED,
                                                    RUN_COMPLETED_WITH_ERRORS,
                                                    RUN_FAILED, RUN_RUNNING,
                                                    TRIGGER_MANUAL_CLI,
                                                    TRIGGER_SCHEDULED)
from services.article_sync.service import (ArticleSyncError,
                                           apply_article_metadata_changes,
                                           bulk_apply_safe_changes,
                                           check_article_metadata,
                                           dismiss_sync_result,
                                           get_dashboard_summary,
                                           get_sync_result, get_sync_result_row,
                                           list_dashboard, list_sync_history)
from services.article_sync.state import (ACTOR_ADMIN, ACTOR_AGENT,
                                         ACTOR_SYSTEM, ACTOR_TYPES,
                                         QUEUE_STATE_LABELS,
                                         derive_queue_state)

__all__ = [
    'compare_article_metadata', 'CRITICAL', 'REVIEW', 'SAFE', 'UNCHANGED',
    'MetadataFieldDiff', 'MetadataSyncResult', 'APPLICABLE_FIELDS',
    'FIELD_LABELS', 'ArticleSyncError', 'apply_article_metadata_changes',
    'bulk_apply_safe_changes', 'check_article_metadata', 'dismiss_sync_result',
    'get_dashboard_summary', 'get_sync_result', 'get_sync_result_row',
    'list_dashboard', 'list_sync_history', 'ACTOR_ADMIN', 'ACTOR_AGENT', 'ACTOR_SYSTEM',
    'ACTOR_TYPES', 'QUEUE_STATE_LABELS', 'derive_queue_state',
    # Phase 2C.3C — rejalashtirilgan sinxronizatsiya (scheduler).
    'SchedulerAlreadyRunningError', 'SchedulerRunResult',
    'list_recent_scheduler_runs', 'run_scheduled_metadata_sync',
    'select_due_articles', 'SchedulerConfig', 'SchedulerConfigError',
    'scheduler_config_from_env', 'RUN_COMPLETED', 'RUN_COMPLETED_WITH_ERRORS',
    'RUN_FAILED', 'RUN_RUNNING', 'TRIGGER_MANUAL_CLI', 'TRIGGER_SCHEDULED',
]
