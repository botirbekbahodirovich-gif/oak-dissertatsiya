# Olimlar.uz redesign audit

Stack: Flask / Jinja2 / Bootstrap 5.3.3 / Bootstrap Icons / Chart.js. No Tailwind, Node build or React routing.

Existing uncommitted changes were present before this task; backend/data changes are outside this redesign.

## Route inventory (decorator paths; blueprint prefixes apply)

| Source | View | Paths | Templates |
|---|---|---|---|
| app.py | set_region | /api/profile/set-region |  |
| app.py | api_username_check | /api/username/check |  |
| app.py | api_username_suggestions | /api/username/suggestions |  |
| app.py | admin_usernames | /admin/usernames | admin/usernames.html |
| app.py | admin_username_set | /admin/usernames/<int:pid>/set |  |
| app.py | home | / | home.html |
| app.py | index | /dashboard | dashboard.html |
| app.py | stats_page | /stats | stats.html |
| app.py | trends | /trends | trends.html |
| app.py | collaboration | /collaboration | collaboration.html |
| app.py | api_collaboration | /api/collaboration |  |
| app.py | api_collaboration_search | /api/collaboration/search |  |
| app.py | topic_clustering | /clustering | clustering.html |
| app.py | api_clustering | /api/clustering |  |
| app.py | cluster_detail | /clustering/<int:cluster_id> | cluster_detail.html |
| app.py | compare | /compare, /mavzu-tahlili | compare.html |
| app.py | api_mavzu_tahlili | /api/mavzu-tahlili |  |
| app.py | api_mavzu_tahlili_ai | /api/mavzu-tahlili/ai |  |
| app.py | notifications_count | /api/notifications/count |  |
| app.py | notifications_page | /notifications | notifications.html |
| app.py | profile | /profile |  |
| app.py | offline | /offline | offline.html |
| app.py | service_worker | /sw.js |  |
| app.py | online_count | /api/online-count |  |
| app.py | user_count | /api/user-count |  |
| app.py | api_genealogy | /api/genealogy/<path:name> |  |
| app.py | api_genealogy_expand | /api/genealogy/expand/<path:name> |  |
| app.py | genealogy_page | /genealogy/<path:name> | genealogy.html |
| app.py | admin_fix_names | /admin/fix-names |  |
| app.py | top_olimlar | /top-olimlar | top_olimlar.html |
| app.py | about | /about | about.html |
| app.py | team | /team | team.html |
| app.py | contact | /contact | contact.html |
| app.py | course_subscribe | /api/course-subscribe |  |
| app.py | admin_courses | /admin/courses | admin_courses.html |
| app.py | preparation | /preparation | preparation.html |
| app.py | courses | /courses | courses.html |
| app.py | course_interest_toggle | /api/courses/<int:course_id>/interest |  |
| app.py | survey_questions_api | /api/survey/questions |  |
| app.py | survey_submit_api | /api/survey/submit |  |
| app.py | sitemap_xml | /sitemap.xml |  |
| app.py | sitemap_section | /sitemap-<section>.xml |  |
| app.py | robots_txt | /robots.txt |  |
| app.py | og_default_image | /static/og-default.png |  |
| app.py | heatmap | /heatmap | heatmap.html |
| auth.py | login | /login | login.html |
| auth.py | register | /register | register.html |
| auth.py | logout | /logout |  |
| auth.py | google_login | /login/google | login.html, login.html |
| auth.py | google_callback | /login/google/callback | login.html, login.html, login.html |
| auth.py | telegram_login | /login/telegram |  |
| cabinet.py | cabinet | /cabinet | cabinet.html |
| cabinet.py | welcome_choose_type | /cabinet/xush-kelibsiz |  |
| cabinet.py | update_user_type | /cabinet/api/user-type |  |
| cabinet.py | onboarding | /cabinet/onboarding | cabinet_onboarding.html |
| cabinet.py | login | /cabinet/login | cabinet_login.html |
| cabinet.py | register | /cabinet/register | cabinet_register.html |
| cabinet.py | telegram | /cabinet/telegram |  |
| cabinet.py | google_login | /auth/google/login |  |
| cabinet.py | google_callback | /auth/google/callback |  |
| cabinet.py | logout | /cabinet/api/logout |  |
| cabinet.py | profile_save | /cabinet/api/profile/save |  |
| cabinet.py | avatar_upload | /cabinet/api/avatar/upload |  |
| cabinet.py | avatar_remove | /cabinet/api/avatar/remove |  |
| cabinet.py | search_olim | /cabinet/api/search-olim |  |
| cabinet.py | search_advisor | /cabinet/api/search-advisor |  |
| cabinet.py | search_institution | /cabinet/api/search-institution |  |
| cabinet.py | claim | /cabinet/api/claim |  |
| cabinet.py | unclaim | /cabinet/api/unclaim |  |
| cabinet.py | maqola_add | /cabinet/api/maqola/add |  |
| cabinet.py | maqola_edit | /cabinet/api/maqola/edit/<int:id> |  |
| cabinet.py | maqola_delete | /cabinet/api/maqola/delete/<int:id> |  |
| cabinet.py | konf_add | /cabinet/api/konferensiya/add |  |
| cabinet.py | konf_edit | /cabinet/api/konferensiya/edit/<int:id> |  |
| cabinet.py | konf_delete | /cabinet/api/konferensiya/delete/<int:id> |  |
| cabinet.py | ish_add | /cabinet/api/ish/add |  |
| cabinet.py | ish_edit | /cabinet/api/ish/edit/<int:id> |  |
| cabinet.py | ish_delete | /cabinet/api/ish/delete/<int:id> |  |
| cabinet.py | rasm_add | /cabinet/api/rasm/add |  |
| cabinet.py | rasm_delete | /cabinet/api/rasm/delete/<int:id> |  |
| cabinet.py | shogird_add | /cabinet/api/shogird/add |  |
| cabinet.py | shogird_edit | /cabinet/api/shogird/edit/<int:id> |  |
| cabinet.py | shogird_delete | /cabinet/api/shogird/delete/<int:id> |  |
| data.py | data | /data |  |
| data.py | dissertatsiyalar_page | /dissertatsiyalar | dashboard.html |
| data.py | filters | /filters |  |
| data.py | dashboard_search | /api/dashboard/search |  |
| data.py | bookmark_toggle | /api/bookmarks/toggle |  |
| data.py | bookmarks_list | /api/bookmarks |  |
| data.py | search_stats | /search-stats |  |
| data.py | search_summary | /search-summary |  |
| data.py | search_as_opponent | /search-as-opponent |  |
| data.py | olim_profile | /olim/<path:name> |  |
| data.py | profile_by_username | /@<slug> |  |
| data.py | profile_by_bare_username | /<slug> |  |
| data.py | dissertation | /dissertation/<int:id> | dissertation.html |
| data.py | author | /author/<path:name> | author.html |
| data.py | supervisor | /supervisor/<path:name> | supervisor.html |
| data.py | specialization | /specialization/<path:code> | specialization.html |
| data.py | chat | /api/chat |  |
| data.py | oak_ingest | /api/oak/ingest |  |
| data.py | import_oak | /api/oak/import, /api/v1/import-oak |  |
| data.py | scholar_tree | /api/v1/scholar/<int:scholar_id>/tree |  |
| data.py | scholar_tree_by_name | /api/v1/scholar/tree/by-name/<path:name> |  |
| data.py | network_graph | /api/v1/network |  |
| data.py | fix_existing_data | /admin/fix-existing-data |  |
| analytics.py | stats_json | /stats-json |  |
| analytics.py | analytics_data | /analytics-data |  |
| analytics.py | faol | /api/faol |  |
| upload.py | upload_page | /upload | upload.html |
| upload.py | upload_csv | /upload |  |
| upload.py | clear_and_upload | /admin/clear-and-upload |  |
| blueprints/acquisition_survey.py | should_show | /api/acquisition-survey/should-show |  |
| blueprints/acquisition_survey.py | submit | /api/acquisition-survey/submit |  |
| blueprints/acquisition_survey.py | skip | /api/acquisition-survey/skip |  |
| blueprints/admin.py | admin_analytics | /admin/analytics | admin_analytics.html |
| blueprints/admin.py | admin_acquisition | /admin/acquisition | admin_acquisition.html |
| blueprints/admin.py | admin_block_user | /admin/api/block-user |  |
| blueprints/admin.py | admin_unblock_user | /admin/api/unblock-user |  |
| blueprints/admin.py | admin_broadcast | /admin/api/broadcast |  |
| blueprints/admin.py | admin_broadcast_delete | /admin/api/broadcast/delete/<int:id> |  |
| blueprints/admin.py | admin_user_activity | /admin/user-activity/<identifier> | admin_user_activity.html |
| blueprints/admin.py | admin_yangiliklar | /admin/yangiliklar | admin_yangiliklar.html |
| blueprints/admin.py | admin_yangilik_add | /admin/yangiliklar/add | admin_yangilik_form.html, admin_yangilik_form.html |
| blueprints/admin.py | admin_yangilik_edit | /admin/yangiliklar/edit/<int:id> | admin_yangilik_form.html, admin_yangilik_form.html |
| blueprints/admin.py | admin_yangilik_delete | /admin/yangiliklar/delete/<int:id> |  |
| blueprints/admin.py | admin_vacancies | /admin/vacancies | admin_vacancies.html |
| blueprints/admin.py | admin_vacancy_add | /admin/vacancies/add | admin_vacancy_form.html, admin_vacancy_form.html |
| blueprints/admin.py | admin_vacancy_edit | /admin/vacancies/edit/<int:id> | admin_vacancy_form.html, admin_vacancy_form.html |
| blueprints/admin.py | admin_vacancy_delete | /admin/vacancies/delete/<int:id> |  |
| blueprints/admin.py | admin_blog | /admin/blog | admin_blog.html |
| blueprints/admin.py | admin_blog_add | /admin/blog/add | admin_blog_form.html, admin_blog_form.html, admin_blog_form.html |
| blueprints/admin.py | admin_blog_edit | /admin/blog/edit/<int:id> | admin_blog_form.html, admin_blog_form.html, admin_blog_form.html |
| blueprints/admin.py | admin_blog_delete | /admin/blog/delete/<int:id> |  |
| blueprints/admin.py | admin_survey | /admin/survey | admin_survey.html |
| blueprints/admin.py | admin_universities | /admin/universities | admin_universities.html |
| blueprints/admin.py | admin_university_add | /admin/university/add | admin_university_form.html, admin_university_form.html |
| blueprints/admin.py | admin_university_edit | /admin/university/edit/<int:id> | admin_university_form.html, admin_university_form.html |
| blueprints/admin.py | admin_university_logo | /admin/university/logo/<int:id> |  |
| blueprints/admin.py | admin_university_gallery_add | /admin/university/gallery/<int:id> |  |
| blueprints/admin.py | admin_university_gallery_delete | /admin/university/gallery/delete/<int:id> |  |
| blueprints/admin.py | admin_university_delete | /admin/university/delete/<int:id> |  |
| blueprints/admin.py | admin_institutions | /admin/institutions | admin_institutions.html |
| blueprints/admin.py | admin_institutions_rename | /admin/institutions/rename |  |
| blueprints/admin.py | admin_institution_identity_resolve | /admin/institutions/identity/resolve |  |
| blueprints/admin.py | admin_institution_identity_create | /admin/institutions/identity/create |  |
| blueprints/admin.py | admin_institution_identity_merge | /admin/institutions/identity/merge |  |
| blueprints/admin.py | admin_institution_identity_undo | /admin/institutions/identity/undo/<int:event_id> |  |
| blueprints/admin.py | admin_institutions_undo | /admin/institutions/undo/<int:rename_id> |  |
| blueprints/admin.py | admin_journals | /admin/journals | admin_journals.html |
| blueprints/admin.py | admin_journal_add | /admin/journals/add | admin_journal_form.html, admin_journal_form.html |
| blueprints/admin.py | admin_journal_edit | /admin/journals/edit/<int:id> | admin_journal_form.html, admin_journal_form.html |
| blueprints/admin.py | admin_journal_logo | /admin/journals/logo/<int:id> |  |
| blueprints/admin.py | admin_journal_delete | /admin/journals/delete/<int:id> |  |
| blueprints/admin.py | admin_journal_toggle_suspect | /admin/journals/toggle-suspect/<int:id> |  |
| blueprints/admin.py | admin_journals_csv | /admin/journals/csv |  |
| blueprints/admin.py | admin_journals_suspects | /admin/journals/suspects |  |
| blueprints/advisors.py | advisor_directory | /rahbar-topish | rahbar_topish.html |
| blueprints/advisors.py | advisor_profile | /rahbar/<slug> | rahbar_profile.html |
| blueprints/article_import.py | import_new | /admin/articles/import | admin/article_import.html, admin/article_import.html, admin/article_import.html, admin/article_import.html |
| blueprints/article_import.py | import_history | /admin/articles/imports | admin/article_imports.html |
| blueprints/article_import.py | import_batch_detail | /admin/articles/imports/<int:batch_id> | admin/article_import_batch.html |
| blueprints/article_import.py | import_batch_status | /admin/articles/imports/<int:batch_id>/status |  |
| blueprints/article_import.py | import_batch_process | /admin/articles/imports/<int:batch_id>/process |  |
| blueprints/article_import.py | import_batch_cancel | /admin/articles/imports/<int:batch_id>/cancel |  |
| blueprints/article_import.py | import_batch_bulk_approve | /admin/articles/imports/<int:batch_id>/bulk-approve |  |
| blueprints/article_import.py | import_item_detail | /admin/articles/imports/<int:batch_id>/items/<int:item_id> | admin/article_import_item.html |
| blueprints/article_import.py | import_item_approve | /admin/articles/imports/<int:batch_id>/items/<int:item_id>/approve |  |
| blueprints/article_import.py | import_item_reject | /admin/articles/imports/<int:batch_id>/items/<int:item_id>/reject |  |
| blueprints/article_import.py | import_item_skip | /admin/articles/imports/<int:batch_id>/items/<int:item_id>/skip |  |
| blueprints/article_import.py | import_item_retry | /admin/articles/imports/<int:batch_id>/items/<int:item_id>/retry |  |
| blueprints/articles.py | catalog | /maqolalar | maqolalar_katalog.html |
| blueprints/articles.py | fields_index | /maqolalar/yonalishlar | maqolalar_yonalishlar.html |
| blueprints/articles.py | field_detail | /maqolalar/yonalish/<slug> | maqolalar_yonalish.html |
| blueprints/articles.py | article_detail | /maqolalar/<slug> | maqolalar_detail.html |
| blueprints/articles.py | article_read | /maqolalar/<slug>/oqish | maqolalar_oqish.html |
| blueprints/articles.py | article_pdf_stream | /maqolalar/<slug>/pdf |  |
| blueprints/articles.py | article_file | /maqolalar/<slug>/fayl |  |
| blueprints/articles.py | admin_article_metadata_lookup | /admin/articles/metadata/lookup |  |
| blueprints/articles.py | admin_article_metadata_check | /admin/articles/<int:article_id>/metadata/check |  |
| blueprints/articles.py | admin_article_metadata_apply | /admin/articles/<int:article_id>/metadata/apply |  |
| blueprints/articles.py | admin_article_metadata_dashboard | /admin/articles/metadata-sync | admin/article_metadata_sync_dashboard.html |
| blueprints/articles.py | admin_article_metadata_sync_detail | /admin/articles/metadata-sync/<int:sync_id> | admin/article_metadata_sync_detail.html |
| blueprints/articles.py | admin_article_metadata_sync_dismiss | /admin/articles/metadata-sync/<int:sync_id>/dismiss |  |
| blueprints/articles.py | admin_article_metadata_bulk_apply_safe | /admin/articles/metadata-sync/bulk-apply-safe |  |
| blueprints/articles.py | admin_article_metadata_recheck | /admin/articles/<int:article_id>/metadata/recheck |  |
| blueprints/articles.py | admin_article_metadata_history | /admin/articles/<int:article_id>/metadata/history | admin/article_metadata_sync_history.html |
| blueprints/articles.py | admin_articles | /admin/articles | admin/articles.html |
| blueprints/articles.py | admin_article_new | /admin/articles/new | admin/article_form.html, admin/article_form.html |
| blueprints/articles.py | admin_article_edit | /admin/articles/<int:article_id>/edit | admin/article_form.html, admin/article_form.html |
| blueprints/articles.py | admin_article_review | /admin/articles/<int:article_id>/review | admin/article_review.html, admin/article_review.html |
| blueprints/articles.py | admin_article_preview | /admin/articles/<int:article_id>/preview | maqolalar_detail.html |
| blueprints/articles.py | admin_article_delete | /admin/articles/<int:article_id>/delete |  |
| blueprints/articles.py | admin_fields | /admin/scientific-fields | admin/scientific_fields.html |
| blueprints/articles.py | admin_field_review | /admin/scientific-fields/review | admin/scientific_field_review.html |
| blueprints/articles.py | admin_field_new | /admin/scientific-fields/new | admin/scientific_field_form.html, admin/scientific_field_form.html |
| blueprints/articles.py | admin_field_edit | /admin/scientific-fields/<int:field_id>/edit | admin/scientific_field_form.html, admin/scientific_field_form.html |
| blueprints/articles.py | admin_field_delete | /admin/scientific-fields/<int:field_id>/delete |  |
| blueprints/conferences.py | api_conferences | /api/v1/conferences |  |
| blueprints/conferences.py | conferences_hub | /konferensiyalar | conferences_hub.html |
| blueprints/conferences.py | conferences_local | /konferensiyalar/mahalliy |  |
| blueprints/conferences.py | conferences_intl | /konferensiyalar/xalqaro |  |
| blueprints/conferences.py | conference_detail | /konferensiya/<slug> | conference_detail.html |
| blueprints/conferences.py | conference_ics | /konferensiya/<slug>.ics |  |
| blueprints/conferences.py | conference_bookmark | /api/v1/conferences/bookmark |  |
| blueprints/conferences.py | conference_to_roadmap | /api/v1/conferences/roadmap |  |
| blueprints/conferences.py | dispatch_alerts | /api/v1/conferences/dispatch-alerts |  |
| blueprints/conferences.py | admin_conferences | /admin/conferences | admin/conferences.html |
| blueprints/conferences.py | admin_conferences_add | /admin/conferences/add | admin/conference_form.html, admin/conference_form.html, admin/conference_form.html |
| blueprints/conferences.py | admin_conferences_edit | /admin/conferences/edit/<int:cid> | admin/conference_form.html |
| blueprints/conferences.py | admin_conferences_approve | /admin/conferences/approve/<int:cid> |  |
| blueprints/conferences.py | admin_conferences_toggle | /admin/conferences/toggle/<int:cid> |  |
| blueprints/conferences.py | admin_conferences_template | /admin/conferences/template |  |
| blueprints/conferences.py | admin_conferences_export | /admin/conferences/export |  |
| blueprints/conferences.py | admin_conferences_import | /admin/conferences/import | admin/conference_import.html, admin/conference_import.html |
| blueprints/content.py | yangiliklar | /yangiliklar | yangiliklar.html |
| blueprints/content.py | yangilik_detail | /yangiliklar/<int:id> | yangilik_detail.html |
| blueprints/content.py | vacancies | /vacancies | vacancies.html |
| blueprints/content.py | vacancy_detail | /vacancies/<int:id> | vacancy_detail.html |
| blueprints/content.py | blog | /blog | blog.html |
| blueprints/content.py | blog_post | /blog/<slug> | blog_detail.html |
| blueprints/content.py | universities | /universities | universities.html |
| blueprints/content.py | university_profile | /university/<path:name> | university_profile.html |
| blueprints/content.py | journals | /journals | journals.html |
| blueprints/content.py | journal_detail | /journals/<int:id> | journal_detail.html |
| blueprints/councils.py | api_councils | /api/v1/councils |  |
| blueprints/councils.py | councils_page | /councils | councils.html |
| blueprints/councils.py | council_detail | /councils/<path:raqam> | council_detail.html |
| blueprints/data_quality.py | dashboard | /admin/data-quality | admin_data_quality.html, admin_data_quality.html |
| blueprints/data_quality.py | trigger_scan | /admin/data-quality/scan |  |
| blueprints/data_quality.py | resolve_issue | /admin/data-quality/issue/<int:issue_id>/resolve |  |
| blueprints/data_quality.py | duplicate_queue | /admin/data-quality/duplicates | admin_duplicate_review.html, admin_duplicate_review.html |
| blueprints/data_quality.py | review_duplicate | /admin/data-quality/duplicate/<int:dup_id>/review |  |
| blueprints/data_quality.py | export_csv | /admin/data-quality/export.csv |  |
| blueprints/dissertation.py | advisor_invite | /api/advisor/invite |  |
| blueprints/dissertation.py | advisor_respond | /api/advisor/respond |  |
| blueprints/dissertation.py | advisor_my_links | /api/advisor/my-links |  |
| blueprints/dissertation.py | advisor_remove | /api/advisor/remove/<int:link_id> |  |
| blueprints/dissertation.py | advisor_invite_link | /api/advisor/invite-link |  |
| blueprints/dissertation.py | invite_landing | /invite/<token> | dissertation/invite_confirm.html |
| blueprints/dissertation.py | invite_respond | /invite/<token>/respond |  |
| blueprints/dissertation.py | collaborator_list | /api/dissertation/<int:id>/collaborators |  |
| blueprints/dissertation.py | collaborator_invite | /api/dissertation/<int:id>/collaborators/invite |  |
| blueprints/dissertation.py | collaborator_invite_link | /api/dissertation/<int:id>/collaborators/invite-link |  |
| blueprints/dissertation.py | collaborator_permissions | /api/collaborators/<int:cid>/permissions |  |
| blueprints/dissertation.py | collaborator_remove | /api/collaborators/<int:cid>/remove |  |
| blueprints/dissertation.py | collaborator_respond | /api/collaborators/respond |  |
| blueprints/dissertation.py | workspace_list | /workspace | dissertation/list.html |
| blueprints/dissertation.py | project_create | /api/dissertation/create |  |
| blueprints/dissertation.py | project_update | /api/dissertation/<int:id>/update |  |
| blueprints/dissertation.py | project_assign_advisor | /api/dissertation/<int:id>/assign-advisor |  |
| blueprints/dissertation.py | project_archive | /api/dissertation/<int:id>/archive |  |
| blueprints/dissertation.py | blocks_tree | /api/dissertation/<int:id>/blocks |  |
| blueprints/dissertation.py | block_create | /api/dissertation/<int:id>/blocks/create |  |
| blueprints/dissertation.py | block_rename | /api/blocks/<int:block_id>/rename |  |
| blueprints/dissertation.py | block_delete | /api/blocks/<int:block_id>/delete |  |
| blueprints/dissertation.py | block_reorder | /api/blocks/<int:block_id>/reorder |  |
| blueprints/dissertation.py | workspace_first | /workspace/<int:id>/first |  |
| blueprints/dissertation.py | editor_page | /workspace/<int:id>/edit/<int:block_id> | dissertation/editor.html |
| blueprints/dissertation.py | block_save | /api/blocks/<int:block_id>/save |  |
| blueprints/dissertation.py | block_lock | /api/blocks/<int:block_id>/lock |  |
| blueprints/dissertation.py | block_unlock | /api/blocks/<int:block_id>/unlock |  |
| blueprints/dissertation.py | versions_list | /api/blocks/<int:block_id>/versions |  |
| blueprints/dissertation.py | version_content | /api/blocks/<int:block_id>/versions/<int:version_id> |  |
| blueprints/dissertation.py | version_restore | /api/blocks/<int:block_id>/restore/<int:version_id> |  |
| blueprints/dissertation.py | block_upload_image | /api/blocks/<int:block_id>/upload-image |  |
| blueprints/dissertation.py | annotations_list | /api/blocks/<int:block_id>/annotations |  |
| blueprints/dissertation.py | annotation_create | /api/blocks/<int:block_id>/annotations/create |  |
| blueprints/dissertation.py | annotation_reply | /api/annotations/<int:id>/reply |  |
| blueprints/dissertation.py | annotation_resolve | /api/annotations/<int:id>/resolve |  |
| blueprints/dissertation.py | annotation_reopen | /api/annotations/<int:id>/reopen |  |
| blueprints/dissertation.py | annotation_delete | /api/annotations/<int:id>/delete |  |
| blueprints/dissertation.py | block_review_status | /api/blocks/<int:block_id>/review-status |  |
| blueprints/dissertation.py | submit_for_review | /api/dissertation/<int:id>/submit-for-review |  |
| blueprints/dissertation.py | finish_review | /api/dissertation/<int:id>/finish-review |  |
| blueprints/dissertation.py | diss_notifications | /api/diss-notifications |  |
| blueprints/dissertation.py | diss_notifications_mark_read | /api/diss-notifications/mark-read |  |
| blueprints/dissertation.py | preview_page | /workspace/<int:id>/preview | dissertation/preview.html |
| blueprints/dissertation.py | export_docx | /workspace/<int:id>/export-docx | dissertation/preview_error.html, dissertation/preview_error.html |
| blueprints/grants.py | grants_list | /grants | grants.html |
| blueprints/grants.py | grant_detail | /grants/<int:id> |  |
| blueprints/grants.py | grant_detail_slug | /grants/<slug> |  |
| blueprints/grants.py | api_grants_v2 | /api/grants |  |
| blueprints/grants.py | api_track | /api/grants/<int:grant_id>/track |  |
| blueprints/grants.py | api_update_status | /api/grants/<int:grant_id>/update-status |  |
| blueprints/grants.py | api_my_tracked | /api/grants/my-tracked |  |
| blueprints/grants.py | api_deadline_alerts | /api/grants/deadline-alerts |  |
| blueprints/grants.py | api_grants_legacy | /api/v1/grants |  |
| blueprints/grants.py | track_grant_legacy | /api/v1/grants/track |  |
| blueprints/grants.py | grant_reminders | /api/v1/grants/reminders |  |
| blueprints/grants.py | admin_grants | /admin/grants | admin/grants.html |
| blueprints/grants.py | admin_grants_add | /admin/grants/add | admin/grant_form.html, admin/grant_form.html, admin/grant_form.html |
| blueprints/grants.py | admin_grants_edit | /admin/grants/edit/<int:id> | admin/grant_form.html |
| blueprints/grants.py | admin_grants_delete | /admin/grants/delete/<int:id> |  |
| blueprints/grants.py | admin_grants_toggle_featured | /admin/grants/toggle-featured/<int:id> |  |
| blueprints/grants.py | admin_grants_seed | /admin/grants/seed |  |
| blueprints/journal_check.py | api_verify | /api/journals/verify |  |
| blueprints/journal_check.py | checker_page | /jurnal-tekshirish | jurnal_tekshirish.html |
| blueprints/kafedra.py | index | / | kafedra/index.html |
| blueprints/kafedra.py | document_form | /<doc_type>/ |  |
| blueprints/kafedra.py | sample_preview | /<doc_type>/namuna/ |  |
| blueprints/kafedra.py | sample_download | /<doc_type>/namuna/download |  |
| blueprints/kafedra.py | generate | /<doc_type>/generate |  |
| blueprints/kafedra.py | status | /status/<doc_id> |  |
| blueprints/kafedra.py | result | /generated/<doc_id>/ | kafedra/result.html |
| blueprints/kafedra.py | download | /generated/<doc_id>/download |  |
| blueprints/kafedra.py | history | /tarix/ | kafedra/tarix.html |
| blueprints/legal.py | oferta | /oferta | legal_oferta.html |
| blueprints/legal.py | refund_policy | /tolovni-qaytarish | legal_refund.html |
| blueprints/legal.py | privacy_policy | /maxfiylik | legal_privacy.html |
| blueprints/map.py | research_map | /tadqiqot-xaritasi | map.html |
| blueprints/map.py | regions_stats | /api/regions/stats |  |
| blueprints/maqolalar.py | maqolalar_page | /maqolalar-tahlili | maqolalar_tahlili.html |
| blueprints/maqolalar.py | api_search | /api/maqolalar/search |  |
| blueprints/maqolalar.py | api_bookmark_toggle | /api/maqolalar/bookmark |  |
| blueprints/maqolalar.py | api_bookmark_ids | /api/maqolalar/bookmark/ids |  |
| blueprints/maqolalar.py | saved_articles_page | /cabinet/maqolalar | maqolalar_saqlangan.html |
| blueprints/messages.py | inbox | /messages | messages/inbox.html |
| blueprints/messages.py | thread | /messages/<int:conversation_id> | messages/thread.html |
| blueprints/messages.py | start_conversation | /api/messages/start |  |
| blueprints/messages.py | send_message | /api/messages/<int:conversation_id>/send |  |
| blueprints/messages.py | upload_attachment | /api/messages/<int:conversation_id>/upload |  |
| blueprints/messages.py | poll_messages | /api/messages/<int:conversation_id>/poll |  |
| blueprints/messages.py | older_messages | /api/messages/older/<int:conversation_id> |  |
| blueprints/messages.py | unread_count | /api/messages/unread-count |  |
| blueprints/messages.py | messages_cleanup | /api/v1/messages/cleanup |  |
| blueprints/notifications.py | active_notifications | /api/v1/notifications/active |  |
| blueprints/notifications.py | dismiss_notification | /api/v1/notifications/dismiss |  |
| blueprints/notifications.py | admin_post_broadcast | /admin/api/notifications/broadcast |  |
| blueprints/notifications.py | admin_post_alert | /admin/api/notifications/alert |  |
| blueprints/notifications.py | notification_prefs | /api/v1/notifications/prefs |  |
| blueprints/olimlar_catalog.py | olimlar_page | /olimlar | olimlar_catalog.html |
| blueprints/olimlar_catalog.py | api_olimlar | /api/olimlar |  |
| blueprints/olimlar_catalog.py | api_olimlar_group | /api/olimlar/group |  |
| blueprints/olimlar_catalog.py | api_olimlar_ratings | /api/olimlar/ratings |  |
| blueprints/olimlar_catalog.py | api_follow | /api/olimlar/<slug>/follow |  |
| blueprints/payments.py | pay_create | /api/pay/create |  |
| blueprints/payments.py | atmos_callback | /api/pay/atmos/callback |  |
| blueprints/payments.py | pay_return | /pay/return | pay_return.html |
| blueprints/payments.py | pay_status | /api/pay/status/<int:payment_id> |  |
| blueprints/payments.py | premium_page | /premium | premium.html |
| blueprints/payments.py | my_payments | /cabinet/tolovlar | payments_history.html |
| blueprints/payments.py | admin_payments | /admin/payments | admin_payments.html |
| blueprints/payments.py | admin_refund | /admin/payments/<int:payment_id>/refund |  |
| blueprints/ranking.py | api_scholars | /api/v1/ranking/scholars |  |
| blueprints/ranking.py | api_institutions | /api/v1/ranking/institutions |  |
| blueprints/ranking.py | ranking_page | /reyting |  |
| blueprints/ranking.py | ranking_institutions_page | /reyting/tashkilotlar |  |
| blueprints/ranking.py | admin_ranking | /admin/reyting | admin_ranking.html |
| blueprints/ranking.py | admin_ranking_delete | /admin/reyting/delete/<int:id> |  |
| blueprints/reminders.py | process_reminders | /api/v1/reminders/process |  |
| blueprints/reminders.py | upcoming_reminders | /api/v1/reminders/upcoming |  |
| blueprints/reminders.py | reminders_page | /reminders | reminders.html |
| blueprints/reminders.py | admin_reminders | /admin/reminders |  |
| blueprints/reminders.py | admin_reminder_add | /admin/reminders/add |  |
| blueprints/reminders.py | admin_reminder_edit | /admin/reminders/edit/<int:id> |  |
| blueprints/reminders.py | admin_reminder_delete | /admin/reminders/delete/<int:id> |  |
| blueprints/reminders.py | admin_reminder_send_now | /admin/reminders/send-now/<int:id> |  |
| blueprints/reminders.py | admin_reminder_resend | /admin/reminders/resend/<int:id> |  |
| blueprints/reyting.py | reyting_page | /reyting/ball | reyting.html |
| blueprints/reyting.py | api_orgs | /api/v1/reyting/orgs |  |
| blueprints/reyting.py | api_olimlar | /api/v1/reyting/olimlar |  |
| blueprints/roadmap.py | dashboard | /reja | roadmap/dashboard.html |
| blueprints/roadmap.py | wizard | /reja/wizard | roadmap/wizard.html |
| blueprints/roadmap.py | plan_create | /api/reja/create |  |
| blueprints/roadmap.py | plan_update | /api/reja/update |  |
| blueprints/roadmap.py | pub_add | /api/reja/pub/add |  |
| blueprints/roadmap.py | pub_update | /api/reja/pub/<int:pid>/update |  |
| blueprints/roadmap.py | pub_delete | /api/reja/pub/<int:pid>/delete |  |
| blueprints/roadmap.py | meeting_add | /api/reja/meeting/add |  |
| blueprints/roadmap.py | meeting_toggle | /api/reja/meeting/<int:mid>/toggle |  |
| blueprints/roadmap.py | meeting_delete | /api/reja/meeting/<int:mid>/delete |  |
| blueprints/roadmap.py | conf_add | /api/reja/conf/add |  |
| blueprints/roadmap.py | conf_status | /api/reja/conf/<int:cid>/status |  |
| blueprints/roadmap.py | conf_delete | /api/reja/conf/<int:cid>/delete |  |
| blueprints/saved.py | saved_toggle | /api/saved/toggle |  |
| blueprints/saved.py | saved_ids | /api/saved/ids |  |
| blueprints/saved.py | saved_notes | /api/saved/<int:dissertation_id>/notes |  |
| blueprints/saved.py | saved_page | /cabinet/saqlangan | saved_dissertations.html |
| blueprints/share.py | olim_tree_share | /olim/<path:name>/daraxt | share/tree.html |
| blueprints/share.py | olim_og_image | /olim/<path:name>/og-image.png |  |
| blueprints/subscriptions.py | list_subscriptions | /api/v1/subscriptions |  |
| blueprints/subscriptions.py | add_subscription | /api/v1/subscriptions |  |
| blueprints/subscriptions.py | remove_subscription | /api/v1/subscriptions/remove |  |
| blueprints/subscriptions.py | toggle_subscription | /api/v1/subscriptions/toggle |  |
| blueprints/topic_analysis.py | tahlil_page | /tahlil | topic_analysis.html |
| blueprints/topic_analysis.py | tahlil_run | /tahlil/run |  |
| blueprints/topic_analysis.py | tahlil_history | /tahlil/history |  |
| blueprints/univer.py | univer_home | /univer | univer/landing.html, univer/dashboard.html, univer/expired.html |
| blueprints/univer.py | univer_olimlar | /univer/olimlar | univer/olimlar.html |
| blueprints/univer.py | univer_dissertations | /univer/dissertatsiyalar | univer/dissertations.html |
| blueprints/univer.py | univer_doktorantlar | /univer/doktorantlar | univer/doktorantlar.html |
| blueprints/univer.py | univer_sozlamalar | /univer/sozlamalar | univer/sozlamalar.html |
| blueprints/univer.py | api_univer_olimlar | /univer/api/olimlar |  |
| blueprints/univer.py | api_univer_dissertations | /univer/api/dissertations |  |
| blueprints/univer.py | api_univer_export | /univer/api/dissertations/export.csv |  |
| blueprints/univer.py | api_doktorant_detail | /univer/api/doktorant/<int:uid> |  |
| blueprints/univer.py | api_staff_add | /univer/api/staff/add |  |
| blueprints/univer.py | api_staff_role | /univer/api/staff/<int:sid>/role |  |
| blueprints/univer.py | api_staff_suspend | /univer/api/staff/<int:sid>/suspend |  |
| blueprints/univer.py | api_privacy | /univer/api/privacy |  |
| blueprints/univer.py | univer_invite_landing | /univer/invite/<token> | univer/invite.html, univer/invite.html, univer/invite.html, univer/invite.html |
| blueprints/univer.py | univer_invite_respond | /univer/invite/<token>/respond |  |
| blueprints/univer.py | admin_licenses | /admin/universities/licenses | admin_univer_licenses.html |
| blueprints/univer.py | admin_license_create | /admin/universities/licenses/create |  |
| blueprints/univer.py | admin_license_detail | /admin/universities/licenses/<int:lid> | admin_univer_license_detail.html |
| blueprints/univer.py | admin_license_staff_add | /admin/universities/licenses/<int:lid>/staff/add |  |
| blueprints/univer.py | admin_license_staff_action | /admin/universities/licenses/<int:lid>/staff/<int:sid> |  |
| blueprints/univer.py | admin_license_invite_link | /admin/universities/licenses/<int:lid>/invite-link |  |
| blueprints/univer.py | univer_conferences_page | /univer/konferensiyalar | univer/konferensiyalar.html |
| blueprints/univer.py | api_uni_conference_add | /univer/api/conferences/add |  |
| blueprints/univer.py | api_uni_conference_update | /univer/api/conferences/<int:cid>/update |  |
| blueprints/univer.py | api_uni_conference_toggle | /univer/api/conferences/<int:cid>/toggle |  |
| blueprints/univer.py | univer_journals_page | /univer/jurnallar | univer/jurnallar.html |
| blueprints/univer.py | api_uni_journal_request | /univer/api/journals/request |  |
| blueprints/univer.py | admin_journal_approve | /admin/journals/approve/<int:jid> |  |
| blueprints/univer.py | api_doctorant_invites | /univer/api/doktorant/invites |  |
| blueprints/univer.py | api_digest_pref | /univer/api/digest-pref |  |
| blueprints/univer.py | dispatch_univer_digest | /api/v1/univer/dispatch-digest |  |
| blueprints/univer.py | univer_reports_page | /univer/hisobotlar | univer/hisobotlar.html |
| blueprints/univer.py | univer_report_generate | /univer/hisobot | univer/report.html |
| blueprints/univer.py | api_public_profile | /univer/api/public-profile |  |
| blueprints/xarita.py | mening_xaritam | /xarita/mening | dissertatsiya_xaritasi.html |
| blueprints/xarita.py | set_supervisor | /xarita/supervisor/set |  |
| blueprints/xarita.py | xarita_data | /api/xarita/data |  |

## Visual audit

- Shared shell already has keyboard navigation, search dialog, mobile drawer and account gates. Preserve these.
- Home has animated decorative cards, placeholder visuals and excessive promotional blocks. Replace with discovery and existing server data.
- Existing tokens have inconsistent neutrals, a 1600px container, Noto/Georgia fallback, Manrope headings, and rainbow chart palette.
- Many templates embed local CSS and raw colors. Migrate CSS declarations to semantic tokens without changing API payloads or forms.
- Public database-backed pages require PostgreSQL; use isolated fixtures for preview and report live-data validation separately.
- Existing global search only searches dissertations. Do not suggest an unsupported cross-entity search.

## New design contract

Source Serif 4 600 for academic headings; Inter 400/500/600 for controls. 1280px container, 16px mobile gutters. Orange brand #E8631A, blue action #0056D6, navy AI #1F2A44. Cards 8px; controls and badges 4px. Light only. DESIGN_VARIANCE=3, MOTION_INTENSITY=2, VISUAL_DENSITY=5.
