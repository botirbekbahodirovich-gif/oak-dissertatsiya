# -*- coding: utf-8 -*-
"""Bosh sahifa "Mening profilim" bloki, chap tezkor panel va /api/me/badges.

Lokal Postgres yo'q: services/my_scholar qalbaki ulanish bilan tekshiriladi
(kursor FILTRLAMAYDI — u so'rov turiga qarab tayyor javob qaytaradi; SQL
semantikasi yuborilgan matn ustida tekshiriladi). Sahifa testlari
app.test_client() bilan, servis funksiyalari patch qilinib ishlaydi.
"""
import re
import unittest
from unittest import mock

import app as app_module
from app import app
from services import my_scholar

OLIM = 'Karimov Anvar Baxtiyorovich'


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._rows, self.description = [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = ' '.join(str(sql).split())
        self.store['sql'].append((s, params))
        for table in self.store.get('missing', ()):
            if 'FROM %s' % table in s:
                raise RuntimeError('relation "%s" does not exist' % table)
        self._rows, cols = self._dispatch(s)
        self.description = [(c,) for c in cols] if cols else None

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def _dispatch(self, s):
        st = self.store
        if s.startswith(('SAVEPOINT', 'RELEASE', 'ROLLBACK')):
            return [], None
        if 'FROM cabinet_users' in s:
            return ([st['cabinet']] if st.get('cabinet') else []), \
                ['id', 'email', 'telegram_username', 'olim_name']
        if 'FROM olim_profiles WHERE cabinet_user_id' in s:
            return ([(st['claimed'],)] if st.get('claimed') else []), ['olim_name']
        if s.startswith('SELECT * FROM olim_profiles'):
            prof = st.get('profile')
            return ([tuple(prof.values())] if prof else []), list((prof or {}).keys())
        if 'FROM olim_maqolalar' in s:
            return [(st.get('pubs', 0),)], ['count']
        if 'COUNT(*) FROM messages' in s:
            return [(st.get('unread_messages', 0),)], ['count']
        if 'FROM messages m' in s and 'ORDER BY m.created_at DESC' in s:
            return [('Rahimova Dilnoza', 'Assalomu alaykum, maqolangiz bo\'yicha savolim bor edi', 7)], \
                ['u', 'b', 'c']
        if 'FROM diss_notifications' in s:
            return [(st.get('diss_unread', 0),)], ['count']
        if 'FROM user_alerts' in s:
            return [(st.get('alerts_unread', 0),)], ['count']
        if s.startswith('SELECT daraja, sana'):
            return st.get('own', []), ['daraja', 'sana', 'ixtisoslik', 'ixtisoslik_nomi',
                                       'muassasa', 'ilmiy_rahbar', 'mavzu']
        if 'COUNT(DISTINCT LOWER(TRIM(g.olim)))' in s:
            return [(st.get('gen2', 0),)], ['count']
        if 'COUNT(DISTINCT LOWER(TRIM(olim)))' in s:
            return [(st.get('students', 0),)], ['count']
        if 'opponent_1' in s:
            return [(st.get('opponent', 0),)], ['count']
        return [], None


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _store(**kw):
    s = {'sql': [], 'cabinet': (5, 'karimov@example.test', 'akarimov', OLIM),
         'profile': {'olim_name': OLIM, 'last_name': 'Karimov', 'first_name': 'Anvar',
                     'patronymic': 'Baxtiyorovich', 'academic_degree': 'DSc',
                     'academic_rank': 'professor', 'ixtisoslik': '05.01.01',
                     'institution': 'Toshkent davlat texnika universiteti',
                     'position': 'kafedra mudiri', 'photo_url': None,
                     'telegram_url': 'https://t.me/akarimov',
                     'orcid_url': 'https://orcid.org/0000-0002-1825-0097',
                     'scholar_url': 'https://scholar.google.com/citations?user=abc'},
         'own': [('PhD', '12.05.2009', '05.01.01', 'Axborot texnologiyalari', 'TDTU', 'Rahbar A', 'Mavzu 1'),
                 ('DSc', '20.11.2018', '05.01.01', 'Axborot texnologiyalari', 'TDTU', '', 'Mavzu 2')],
         'students': 14, 'gen2': 9, 'opponent': 31, 'pubs': 4,
         'unread_messages': 2, 'diss_unread': 3, 'alerts_unread': 1}
    s.update(kw)
    return s


class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_admin = False

    def __init__(self, uid=1, email='karimov@example.test', username='akarimov'):
        self.id, self.email, self.username = uid, email, username

    def get_id(self):
        return str(self.id)


class _ServiceCase(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(my_scholar, '_cache', return_value=None),
            mock.patch('blueprints.ranking.get_scholar_h_index',
                       return_value={'rank': 12, 'h_scopus': 11, 'h_wos': 7, 'h_scholar': 19,
                                     'orcid': None, 'snapshot_date': '2026-10-01'}),
            mock.patch('blueprints.reyting._build_rating', return_value={'scholars': [
                {'name': 'Boshqa Olim', 'codes': ['05.01.01']},
                {'name': 'Karimov Anvar Baxtiyorovich', 'codes': ['05.01.01', '05.01.02']},
                {'name': 'Uchinchi Olim', 'codes': ['05.01.01']},
                {'name': 'Begona Olim', 'codes': ['10.00.01']},
            ]}),
            mock.patch('utils.olim_photos._load_map', return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from utils import olim_photos
        olim_photos.clear_cache()
        self.addCleanup(olim_photos.clear_cache)

    def build(self, store, user=None):
        self.store = store
        with mock.patch.object(my_scholar, '_connect', return_value=_FakeConn(store)), \
                app.test_request_context('/'):
            return my_scholar.build_home_profile(user or _User())

    @staticmethod
    def cells(profile):
        return {c['label']: c for row in profile['rows'] for c in row}


class LinkedUserTests(_ServiceCase):
    def test_full_card(self):
        p = self.build(_store())
        self.assertEqual(p['status'], 'linked')
        self.assertEqual(p['name'], 'Karimov Anvar Baxtiyorovich')
        self.assertEqual(p['degree_line'], 'DSc, professor · Axborot texnologiyalari (05.01.01)')
        self.assertEqual(p['org_line'], 'Toshkent davlat texnika universiteti, kafedra mudiri')
        self.assertEqual(p['profile_url'], '/olim/Karimov%20Anvar%20Baxtiyorovich')
        self.assertEqual(p['school_url'], '/genealogy/Karimov%20Anvar%20Baxtiyorovich')
        c = self.cells(p)
        self.assertEqual(c['Shogirdlar']['value'], 14)
        self.assertEqual(c['Opponentlik']['value'], 31)
        self.assertEqual(c['Himoya']['value'], 'DSc, 20.11.2018')
        self.assertEqual(c['h-indeks']['value'], 11)
        self.assertEqual(c['Nashrlar']['value'], 4)
        self.assertEqual(c['Telegram']['value'], '@akarimov')
        self.assertEqual(c['Email']['value'], 'karimov@example.test')
        self.assertEqual(c['ORCID']['value'], '0000-0002-1825-0097')
        self.assertEqual(c['Google Scholar']['href'], 'https://scholar.google.com/citations?user=abc')
        self.assertEqual(p['badges'], {'messages': 2, 'notifications': 4})
        self.assertEqual(p['latest_message']['url'], '/messages/7')

    def test_rating_is_specialty_rank_and_says_it_is_score_based(self):
        c = self.cells(self.build(_store()))
        self.assertEqual(c['Reyting (mutaxassislik)']['value'], "2-o'rin / 3")
        self.assertIn("Ball bo'yicha reyting", c['Reyting (mutaxassislik)']['title'])

    def test_second_generation_is_marked_approximate_and_links_to_tree(self):
        c = self.cells(self.build(_store()))['Shogirdlarning shogirdlari']
        self.assertEqual(c['value'], '≈9')
        self.assertTrue(c['approx'])
        self.assertIn('Taxminiy', c['title'])
        self.assertTrue(c['href'].startswith('/genealogy/'))

    def test_second_generation_sql_excludes_the_scholar_himself(self):
        self.build(_store())
        sql = next(s for s, _ in self.store['sql'] if 'g.olim' in s)
        self.assertIn('LOWER(TRIM(g.olim)) <> LOWER(TRIM(%s))', sql)

    def test_citations_are_dash_while_unreliable(self):
        self.assertEqual(self.cells(self.build(_store()))['Iqtiboslar']['value'], '—')

    def test_placeholder_telegram_email_is_hidden(self):
        user = _User(email='telegram_123@auth.local')
        store = _store(cabinet=(5, 'telegram_123@auth.local', None, OLIM))
        store['profile'].update(telegram_url=None)
        c = self.cells(self.build(store, user))
        self.assertEqual(c['Email']['value'], '—')
        self.assertEqual(c['Telegram']['value'], '—')

    def test_unsafe_profile_links_are_dropped(self):
        store = _store()
        store['profile'].update(scholar_url='javascript:alert(1)', telegram_url='javascript:x')
        c = self.cells(self.build(store))
        self.assertEqual(c['Google Scholar']['value'], '—')
        self.assertEqual(c['Google Scholar']['href'], '')

    def test_missing_message_tables_do_not_break_the_card(self):
        p = self.build(_store(missing=('messages', 'diss_notifications', 'user_alerts')))
        self.assertEqual(p['status'], 'linked')
        self.assertEqual(p['badges'], {'messages': 0, 'notifications': 0})
        self.assertIsNone(p['latest_message'])

    def test_claim_via_olim_profiles_when_cabinet_name_is_empty(self):
        p = self.build(_store(cabinet=(5, 'karimov@example.test', None, None), claimed=OLIM))
        self.assertEqual(p['status'], 'linked')


class UnlinkedAndGuestTests(_ServiceCase):
    def test_unlinked_user_gets_the_invite(self):
        p = self.build(_store(cabinet=(5, 'x@example.test', None, None), claimed=None))
        self.assertEqual(p['status'], 'unlinked')
        self.assertEqual(p['onboarding_url'], '/cabinet/onboarding')

    def test_placeholder_cabinet_name_is_not_a_link(self):
        p = self.build(_store(cabinet=(5, 'x@example.test', None, 'cabinet_5'), claimed=None))
        self.assertEqual(p['status'], 'unlinked')

    def test_user_without_cabinet_row_is_unlinked(self):
        self.assertEqual(self.build(_store(cabinet=None))['status'], 'unlinked')

    def test_guest_gets_nothing(self):
        guest = mock.Mock(is_authenticated=False)
        self.assertIsNone(self.build(_store(), guest))

    def test_db_down_hides_the_block(self):
        with mock.patch.object(my_scholar, '_connect', side_effect=RuntimeError('down')), \
                app.test_request_context('/'):
            self.assertIsNone(my_scholar.build_home_profile(_User()))


class BadgeCountTests(unittest.TestCase):
    def counts(self, store):
        with mock.patch.object(my_scholar, '_connect', return_value=_FakeConn(store)):
            return my_scholar.badge_counts(1)

    def test_personal_counts_only(self):
        store = _store()
        self.assertEqual(self.counts(store), {'messages': 2, 'notifications': 4})
        sqls = [s for s, _ in store['sql']]
        self.assertFalse(any('FROM notifications' in s for s in sqls),
                         'global scraper notifications must not be counted')
        diss_sql, diss_params = next((s, p) for s, p in store['sql'] if 'diss_notifications' in s)
        self.assertIn('event_type <> ALL(%s)', diss_sql)
        self.assertIn('new_message', diss_params[1])

    def test_db_down_returns_zeros(self):
        with mock.patch.object(my_scholar, '_connect', side_effect=RuntimeError('down')):
            self.assertEqual(my_scholar.badge_counts(1), {'messages': 0, 'notifications': 0})


LINKED = {
    'status': 'linked', 'olim_name': OLIM, 'name': OLIM,
    'degree_line': 'DSc, professor · Axborot texnologiyalari (05.01.01)',
    'org_line': 'TDTU, kafedra mudiri', 'compact_line': 'DSc · 05.01.01 · TDTU',
    'interests': [], 'photo_url': None, 'profile_url': '/olim/x', 'school_url': '/genealogy/x',
    'edit_url': '/cabinet',
    'rows': [[{'label': 'Reyting (mutaxassislik)', 'value': "2-o'rin / 3",
               'title': "Ball bo'yicha reyting (h-indeks emas)", 'href': '', 'approx': False}],
             [{'label': 'Shogirdlarning shogirdlari', 'value': '≈9', 'title': 'Taxminiy',
               'href': '/genealogy/x', 'approx': True}]],
    'compact': [{'label': '2-avlod', 'value': '≈9', 'title': 'Taxminiy', 'href': '', 'approx': True}],
    'badges': {'messages': 2, 'notifications': 4},
    'latest_message': {'sender': 'Rahimova Dilnoza', 'snippet': 'Salom', 'url': '/messages/7'},
}


class HomePageViewTests(unittest.TestCase):
    def html(self, user=None, profile=None, link=None, path='/'):
        client = app.test_client()
        original = app.login_manager._user_callback
        with mock.patch('services.my_scholar.build_home_profile', return_value=profile), \
                mock.patch('services.my_scholar.scholar_link', return_value=link):
            if user is not None:
                app.login_manager._user_callback = lambda uid: user
                with client.session_transaction() as sess:
                    sess['_user_id'] = '1'
                    sess['_fresh'] = True
            try:
                return client.get(path).get_data(as_text=True)
            finally:
                app.login_manager._user_callback = original

    def test_guest_sees_no_profile_block_and_a_guest_rail(self):
        html = self.html()
        self.assertNotIn('ol-mycard', html)
        rail = re.search(r'<nav class="ol-rail".*?</nav>', html, re.S).group(0)
        self.assertIn('href="/login"', rail)
        self.assertIn('https://t.me/olimlar_uz', rail)
        self.assertIn('data-rail-search', rail)
        self.assertNotIn('href="/messages"', rail)
        self.assertNotIn('Mening profilim', rail)

    def test_unlinked_user_sees_the_invite(self):
        html = self.html(_User(), {'status': 'unlinked', 'onboarding_url': '/cabinet/onboarding',
                                   'badges': {'messages': 0, 'notifications': 0}})
        self.assertIn("Profilingizni toping va bog'lang", html)
        self.assertIn('href="/cabinet/onboarding"', html)
        self.assertNotIn('id="ol-mycard"', html)

    def test_linked_user_sees_the_card_above_search(self):
        html = self.html(_User(), LINKED, {'olim_name': OLIM, 'profile_url': '/olim/x',
                                           'school_url': '/genealogy/x'})
        self.assertIn('id="ol-mycard"', html)
        self.assertLess(html.index('id="ol-mycard"'), html.index('id="hx-search-form"'))
        self.assertIn('aria-expanded="true" aria-controls="ol-mycard-full"', html)
        self.assertIn('aria-expanded="false" aria-controls="ol-mycard-full"', html)
        self.assertIn("title=\"Ball bo&#39;yicha reyting (h-indeks emas)\"", html)
        self.assertIn('≈9', html)
        self.assertIn('2 ta yangi xabar', html)
        self.assertIn('ol-mycard__photo--empty', html)   # rasm yo'q → ikonka

    def test_rail_is_hidden_and_inert_on_load_and_lists_only_existing_features(self):
        html = self.html(_User(), LINKED, {'olim_name': OLIM, 'profile_url': '/olim/x',
                                           'school_url': '/genealogy/x'})
        rail = re.search(r'<nav class="ol-rail".*?</nav>', html, re.S).group(0)
        self.assertRegex(rail, r'data-visible="false" data-expanded="false" inert')
        for label in ('Bosh sahifa', 'Mening profilim', 'Xabarlar', 'Bildirishnomalar',
                      'Ilmiy maktabim', 'Tezkor qidiruv', 'Telegram kanal'):
            self.assertIn('aria-label="%s"' % label, rail, label)
        for later in ('Kuzatuvlar', 'Fikr bildirish', 'Kirish'):
            self.assertNotIn(later, rail, later)
        self.assertIn('href="/genealogy/x"', rail)
        bar = re.search(r'<nav class="ol-bottombar".*?</nav>', html, re.S).group(0)
        self.assertIn('inert', bar)
        self.assertIn('data-bottombar-menu', bar)

    def test_header_icons_stay_and_share_the_badge_source(self):
        html = self.html(_User(), LINKED)
        self.assertIn('data-badge="messages"', html)
        self.assertIn('data-badge="notifications"', html)
        self.assertIn('/static/js/olimlar-rail.js', html)
        self.assertNotIn("fetch('/api/notifications/count')", html)
        self.assertNotIn("fetch('/api/messages/unread-count'", html)

    def test_rail_renders_on_other_pages_too(self):
        html = self.html(_User(), None, None, path='/preparation')
        self.assertIn('class="ol-rail"', html)
        self.assertNotIn('id="ol-mycard"', html)


class BadgesApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.original = app.login_manager._user_callback
        self.addCleanup(setattr, app.login_manager, '_user_callback', self.original)

    def login(self):
        app.login_manager._user_callback = lambda uid: _User()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = '1'
            sess['_fresh'] = True

    def test_guest_gets_401(self):
        self.assertEqual(self.client.get('/api/me/badges').status_code, 401)

    def test_logged_in_user_gets_personal_counts(self):
        self.login()
        with mock.patch('services.my_scholar.badge_counts',
                        return_value={'messages': 3, 'notifications': 5}) as bc:
            resp = self.client.get('/api/me/badges')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {'messages': 3, 'notifications': 5})
        self.assertIn('no-store', resp.headers.get('Cache-Control', ''))
        bc.assert_called_once_with(1)

    def test_legacy_count_endpoint_is_personal_now(self):
        self.login()
        with mock.patch('services.my_scholar.badge_counts',
                        return_value={'messages': 3, 'notifications': 5}):
            resp = self.client.get('/api/notifications/count')
        self.assertEqual(resp.get_json(), {'count': 5})


class NotificationTextTests(unittest.TestCase):
    def test_texts_match_the_workspace_banner(self):
        t = app_module._diss_notification_text
        self.assertEqual(t('invite_accepted', {'by': 'ali'}), 'ali taklifingizni qabul qildi.')
        self.assertIn('yangi shogirdi', t('followed_scholar_new_student',
                                          {'scholar_name': 'A', 'student_name': 'B'}))
        self.assertEqual(t('unknown_type', {}), 'Yangi bildirishnoma')

    def test_actionable_invites_are_not_auto_read(self):
        self.assertIn('advisor_invite', app_module._ACTIONABLE_NOTIFICATIONS)
        self.assertIn('collaborator_invite', app_module._ACTIONABLE_NOTIFICATIONS)


if __name__ == '__main__':
    unittest.main()
