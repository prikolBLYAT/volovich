import json
import os
from datetime import date
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from Labs import lab1 as lab


PATH = '/api/v1/labs/lab1/attendance-report'
GROUP = '10000000-0000-0000-0000-000000000001'
OTHER_GROUP = '10000000-0000-0000-0000-000000000002'
STUDENT = '20000000-0000-0000-0000-000000000001'
OTHER_STUDENT = '20000000-0000-0000-0000-000000000002'
LECTURE = '30000000-0000-0000-0000-000000000001'
DEPARTMENT = '40000000-0000-0000-0000-000000000001'
SCHEDULES = [f'50000000-0000-0000-0000-{index:012d}' for index in (1, 2, 3)]


def profile():
    return dict(student_id=STUDENT, group_id=GROUP, first_name='Иван',
                second_name='Иванов', email='ivan@example.org', phone='123',
                student_card_number='12345', status='active')


class AttendanceGraphTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.es = MagicMock()
        self.es.search.return_value = {
            'hits': {'hits': [{'_source': {'lecture_id': LECTURE}}]}
        }
        self.session = MagicMock()
        self.graph_rows = []

        def graph_query(*args, **kwargs):
            self.events.append('neo4j')
            return self.graph_rows

        self.session.run.side_effect = graph_query
        self.driver = MagicMock()
        self.driver.session.return_value.__enter__.return_value = self.session
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

        def pg_connection():
            self.events.append('postgres')
            return self.connection

        self.pg = self.patch('get_pg_conn', side_effect=pg_connection)
        self.patch('get_es_client', return_value=self.es)
        self.patch('neo4j_driver', self.driver)
        self.cache = MagicMock()
        self.cache.mget.return_value = [json.dumps(profile())]
        self.patch('redis_client', self.cache)
        self.mongo = MagicMock()
        self.collection = self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy']
        self.collection.find.return_value = [dict(university_id='university', name='Mongo вуз', institutes=[
            dict(institute_id='institute', name='Mongo институт', departments=[
                dict(department_id=DEPARTMENT, name='Mongo кафедра')])])]
        self.mongo_constructor = self.patch('MongoClient', return_value=self.mongo)
        self.client = TestClient(lab.app)
        self.addCleanup(self.client.close)
        self.params = dict(term='Базы данных', period_start='2026-09-01',
                           period_end='2026-10-01', limit=1)

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(lab, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def set_graph_students(self):
        self.graph_rows = [
            dict(student_ids=[STUDENT], group_id=GROUP,
                 schedule_ids=SCHEDULES[:2], lecture_ids=[LECTURE]),
            dict(student_ids=[OTHER_STUDENT], group_id=OTHER_GROUP,
                 schedule_ids=SCHEDULES[2:], lecture_ids=[LECTURE]),
        ]

    def group_metadata(self):
        return dict(group_id=GROUP, department_id=DEPARTMENT, group_name='Группа',
                    speciality_name='Информатика', speciality_code='09.03.01',
                    degree_level='Бакалавриат')

    def ranked_row(self, student_id=STUDENT, total=2, attended=0):
        return dict(self.group_metadata(), metadata_group_id=GROUP, student_id=student_id,
                    total_classes=total, attended_classes=attended,
                    attendance_percentage=round(attended / total * 100, 2))

    def test_warm_cache_uses_one_main_execute_and_connection(self):
        self.set_graph_students()
        self.cursor.fetchall.return_value = [self.ranked_row()]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        self.pg.assert_called_once()
        self.cursor.execute.assert_called_once()

    def test_storage_order_and_missing_metadata_after_limit(self):
        self.set_graph_students()
        calls = []
        self.es.search.side_effect = lambda **kwargs: (
            calls.append('es') or {'hits': {'hits': [{'_source': {'lecture_id': LECTURE}}]}})
        self.session.run.side_effect = lambda *args, **kwargs: calls.append('neo4j') or self.graph_rows
        self.cursor.execute.side_effect = lambda *args: calls.append('sql')
        self.cache.mget.side_effect = lambda ids: calls.append('redis') or [json.dumps(profile())]
        self.collection.find.side_effect = lambda *args: calls.append('mongo') or []
        self.cursor.fetchall.return_value = [dict(self.ranked_row(), metadata_group_id=None)]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['students'], [])
        self.assertEqual(calls, ['es', 'neo4j', 'sql', 'redis', 'mongo'])
        self.cursor.execute.assert_called_once()
        self.cache.mget.assert_called_once_with([STUDENT])

    def test_hierarchy_is_only_from_mongo_without_pg_hierarchy_joins(self):
        self.set_graph_students()
        self.cursor.fetchall.return_value = [self.ranked_row()]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        student = response.json()['students'][0]
        self.assertEqual([student[key] for key in ('department_name', 'institute_name', 'university_name')],
                         ['Mongo кафедра', 'Mongo институт', 'Mongo вуз'])
        sql = self.cursor.execute.call_args.args[0].lower()
        for table in ('departments', 'institutes', 'universities'):
            self.assertNotIn(table, sql)

    def test_missing_or_incomplete_hierarchy_returns_503(self):
        for docs in ([], [dict(name='Вуз', institutes=[dict(name='Институт', departments=[
                dict(department_id=DEPARTMENT)])])]):
            with self.subTest(docs=docs):
                self.set_graph_students()
                self.cursor.fetchall.return_value = [self.ranked_row()]
                self.collection.find.return_value = docs
                self.assertEqual(self.client.get(PATH, params=self.params).status_code, 503)

    def test_mongo_unavailable_returns_503(self):
        self.set_graph_students()
        self.cursor.fetchall.return_value = [self.ranked_row()]
        self.collection.find.side_effect = RuntimeError('unavailable')
        self.assertEqual(self.client.get(PATH, params=self.params).status_code, 503)

    def test_report_sql_has_no_cte_or_rounding_workarounds(self):
        self.assertNotIn('WITH ', lab.ATTENDANCE_REPORT_SQL)
        self.assertNotIn('jsonb', lab.ATTENDANCE_REPORT_SQL)
        self.assertIn('HAVING ROUND(', lab.ATTENDANCE_REPORT_SQL)
        self.assertFalse(hasattr(lab, 'percentage_rounding_corrections'))

    def test_no_search_hits_skips_other_databases(self):
        self.es.search.return_value = {'hits': {'hits': []}}
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['students'], [])
        self.assertEqual(response.json()['matching_lectures_count'], 0)
        self.session.run.assert_not_called()
        self.pg.assert_not_called()
        self.cache.mget.assert_not_called()
        self.mongo_constructor.assert_not_called()

    def test_no_scheduled_students_skips_postgres(self):
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['students'], [])
        self.assertEqual(self.events, ['neo4j'])
        self.pg.assert_not_called()
        self.cache.mget.assert_not_called()
        self.mongo_constructor.assert_not_called()

    def test_graph_failure_returns_503_without_postgres(self):
        self.session.run.side_effect = RuntimeError('test graph unavailable')
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 503)
        self.pg.assert_not_called()

    def test_graph_precedes_postgres_and_only_top_groups_are_enriched(self):
        self.set_graph_students()
        self.cursor.fetchall.return_value = [self.ranked_row(attended=1)]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['matching_lectures_count'], 1)
        self.assertEqual(len(result['students']), 1)
        student = result['students'][0]
        self.assertEqual(student['student_id'], STUDENT)
        self.assertEqual(student['total_classes'], 2)
        self.assertEqual(student['attended_classes'], 1)
        self.assertEqual(student['attendance_percentage'], 50.0)
        self.assertEqual(student['group_name'], 'Группа')
        self.assertEqual(student['speciality_code'], '09.03.01')
        self.assertEqual(self.events, ['neo4j', 'postgres'])
        self.assertEqual(self.pg.call_count, 1)
        graph_params = self.session.run.call_args.kwargs
        self.assertEqual(graph_params, dict(lecture_ids=[LECTURE],
                                           p_start='2026-09-01', p_end='2026-10-01'))
        main = self.cursor.execute.call_args
        self.assertEqual(main.args[0], lab.ATTENDANCE_REPORT_SQL)
        self.assertEqual(main.args[1][:3], ([STUDENT, OTHER_STUDENT], [GROUP, OTHER_GROUP], [2, 1]))
        self.assertEqual(main.args[1][3:5], ([GROUP, GROUP, OTHER_GROUP], SCHEDULES))
        self.cache.mget.assert_called_once_with([STUDENT])
        self.cache.setex.assert_not_called()

    def test_empty_attendance_range_skips_metadata_and_profiles(self):
        self.set_graph_students()
        self.cursor.fetchall.return_value = []
        response = self.client.get(PATH, params={**self.params, 'min_attendance_pct': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['matching_lectures_count'], 1)
        self.assertEqual(response.json()['students'], [])
        self.assertEqual(self.events, ['neo4j', 'postgres'])
        self.cursor.execute.assert_called_once()
        self.cache.mget.assert_not_called()
        self.mongo_constructor.assert_not_called()

    def test_group_schedule_is_shared_by_all_its_students(self):
        self.graph_rows = [dict(group_id=GROUP, student_ids=[STUDENT, OTHER_STUDENT],
                                schedule_ids=SCHEDULES[:2], lecture_ids=[LECTURE])]
        self.cursor.fetchall.return_value = [self.ranked_row(OTHER_STUDENT), self.ranked_row(attended=1)]
        self.cache.mget.return_value = [
            json.dumps({**profile(), 'student_id': OTHER_STUDENT}), json.dumps(profile())]
        response = self.client.get(PATH, params={**self.params, 'limit': 2})
        self.assertEqual(response.status_code, 200, response.text)
        students = response.json()['students']
        self.assertEqual([(s['student_id'], s['total_classes'], s['attended_classes'],
                           s['attendance_percentage']) for s in students],
                         [(OTHER_STUDENT, 2, 0, 0.0), (STUDENT, 2, 1, 50.0)])
        self.assertEqual(response.json()['matching_lectures_count'], 1)
        self.assertEqual(self.events, ['neo4j', 'postgres'])
        query = self.session.run.call_args.args[0]
        self.assertIn('collect(DISTINCT st.id) AS student_ids', query)
        attendance = self.cursor.execute.call_args_list[0]
        self.assertEqual(attendance.args[1][3:5], ([GROUP, GROUP], SCHEDULES[:2]))
        self.assertEqual(attendance.args[1][0], [STUDENT, OTHER_STUDENT])

    def test_cold_cache_fetches_profile_after_group_metadata(self):
        self.graph_rows = [dict(student_ids=[STUDENT], group_id=GROUP,
                                schedule_ids=SCHEDULES[:1], lecture_ids=[LECTURE])]
        self.cache.mget.return_value = [None]
        self.cursor.fetchall.side_effect = [[self.ranked_row(total=1)], [profile()]]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        student = response.json()['students'][0]
        self.assertEqual(student['student_id'], STUDENT)
        self.assertEqual(student['attendance_percentage'], 0.0)
        self.assertEqual(self.events, ['neo4j', 'postgres', 'postgres'])
        self.assertEqual(self.pg.call_count, 2)
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.assertEqual(self.cursor.execute.call_args.args[1], ([STUDENT],))
        key, ttl, value = self.cache.setex.call_args.args
        self.assertEqual((key, ttl), (STUDENT, 86400))
        self.assertEqual(json.loads(value), profile())

    def test_cache_failures_fall_back_to_one_profile_query(self):
        self.set_graph_students()
        for cached, failure in (([None], None), (['{bad'], None), ([None], RuntimeError('redis'))):
            with self.subTest(cached=cached, failure=failure):
                self.cursor.fetchall.side_effect = [[self.ranked_row()], [profile()]]
                self.cache.mget.return_value = cached
                self.cache.mget.side_effect = failure
                self.cache.setex.side_effect = RuntimeError('redis')
                self.pg.reset_mock()
                self.cursor.execute.reset_mock()
                response = self.client.get(PATH, params=self.params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()['students']), 1)
                self.assertEqual(self.pg.call_count, 2)
                self.assertEqual(self.cursor.execute.call_count, 2)

    def test_stale_cached_group_does_not_override_graph_membership(self):
        self.graph_rows = [dict(student_ids=[STUDENT], group_id=GROUP,
                                schedule_ids=SCHEDULES[:1], lecture_ids=[LECTURE])]
        self.cache.mget.return_value = [json.dumps({**profile(), 'group_id': OTHER_GROUP})]
        self.cursor.fetchall.return_value = [self.ranked_row()]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        students = response.json()['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['group_name'], 'Группа')
        self.assertEqual(self.cursor.execute.call_args.args[1][1], [GROUP])


@unittest.skipUnless(os.getenv('LAB1_SQL_TESTS') == '1', 'Для read-only PostgreSQL задай LAB1_SQL_TESTS=1')
class AttendanceSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Только SELECT: синтетические CTE закрывают настоящие таблицы, запись запрещена сервером.
        cls.conn = psycopg2.connect(
            host=lab.PG_HOST, port=lab.PG_PORT, user=lab.PG_USER, password=lab.PG_PASS,
            dbname=lab.PG_DB, connect_timeout=5, options='-c default_transaction_read_only=on')
        cls.addClassCleanup(cls.conn.close)
        with cls.conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only')
            setting = cur.fetchone()
            if not setting or setting[0] != 'on':
                raise AssertionError('Требуется read-only PostgreSQL')

    def query(self, scopes=None, marks=None, schedule_scope=None, students=None,
              groups=None, specialities=None, schedule=None, lower: float = 0, upper: float = 100, limit=100):
        scopes = scopes if scopes is not None else [(STUDENT, GROUP, 2)]
        schedule_scope = schedule_scope if schedule_scope is not None else [
            (GROUP, SCHEDULES[0]), (GROUP, SCHEDULES[1]), (OTHER_GROUP, SCHEDULES[2])]
        fixtures = [
            ('students', 'id uuid, group_id uuid', students if students is not None else [
                dict(id=s, group_id=g) for s, g, total in scopes]),
            ('schedule', 'id uuid, group_id uuid', schedule if schedule is not None else [
                dict(id=SCHEDULES[0], group_id=GROUP), dict(id=SCHEDULES[1], group_id=GROUP),
                dict(id=SCHEDULES[2], group_id=OTHER_GROUP)]),
            ('attendance', 'student_id uuid, schedule_id uuid, date date, mark int', marks or []),
            ('groups', 'id uuid, department_id uuid, name text, speciality_id uuid',
             groups if groups is not None else [dict(id=GROUP, department_id=DEPARTMENT,
                                                    name='Группа', speciality_id=LECTURE)]),
            ('specialities', 'id uuid, name text, code text, degree_level text',
             specialities if specialities is not None else [dict(id=LECTURE, name='Информатика',
                                                               code='09', degree_level='Бакалавриат')]),
        ]
        ctes = ', '.join(f'{name} AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS data({columns}))'
                         for name, columns, rows in fixtures)
        # Исполняется ровно SQL обработчика, меняются лишь источники данных на CTE.
        sql = 'WITH ' + ctes + ' ' + lab.ATTENDANCE_REPORT_SQL
        params = [json.dumps(rows) for name, columns, rows in fixtures] + [
            [s for s, g, n in scopes], [g for s, g, n in scopes], [n for s, g, n in scopes],
            [g for g, sc in schedule_scope], [sc for g, sc in schedule_scope],
            date(2026, 9, 1), date(2026, 10, 1),
            lower, upper, limit]
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def mark(self, schedule=SCHEDULES[0], day='2026-09-01', mark=1, student=STUDENT):
        return dict(student_id=student, schedule_id=schedule, date=day, mark=mark)

    def test_inclusive_dates_distinct_mark_and_zero(self):
        rows = self.query(scopes=[(STUDENT, GROUP, 2), (OTHER_STUDENT, GROUP, 2)], marks=[
            self.mark(), self.mark(), self.mark(SCHEDULES[1], '2026-10-01'),
            self.mark(SCHEDULES[1], mark=0, student=OTHER_STUDENT),
            self.mark(day='2026-08-31', student=OTHER_STUDENT),
            self.mark(day='2026-10-02', student=OTHER_STUDENT)])
        self.assertEqual([(str(r['student_id']), r['attended_classes'], r['attendance_percentage'])
                          for r in rows], [(OTHER_STUDENT, 0, 0), (STUDENT, 2, 100)])

    def test_schedule_scope_and_relational_group_membership(self):
        for kwargs in (
            dict(schedule_scope=[(GROUP, SCHEDULES[0])], marks=[self.mark(SCHEDULES[1])]),
            dict(marks=[self.mark(SCHEDULES[2])]),
            dict(schedule_scope=[(OTHER_GROUP, SCHEDULES[0])], marks=[self.mark()]),
            dict(schedule_scope=[(GROUP, SCHEDULES[2])], marks=[self.mark(SCHEDULES[2])]),
            dict(students=[dict(id=STUDENT, group_id=OTHER_GROUP)], marks=[self.mark()]),
        ):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.query(**kwargs)[0]['attended_classes'], 0)

    def test_standard_postgres_round_and_inclusive_filter(self):
        row = self.query(scopes=[(STUDENT, GROUP, 32)], marks=[self.mark()],
                         lower=3.13, upper=3.13)[0]
        self.assertEqual(row['attendance_percentage'], Decimal('3.13'))
        row = self.query(scopes=[(STUDENT, GROUP, 32)],
                         marks=[self.mark(), self.mark(SCHEDULES[1])])[0]
        self.assertEqual(row['attendance_percentage'], Decimal('6.25'))
        # PostgreSQL округляет точную половину от нуля: 0.625 -> 0.63.
        self.assertEqual(self.query(scopes=[(STUDENT, GROUP, 160)], marks=[self.mark()])[0]
                         ['attendance_percentage'], Decimal('0.63'))
        self.assertEqual(self.query(scopes=[(STUDENT, GROUP, 32)], marks=[self.mark()],
                                    lower=3.14), [])

    def test_standard_round_odd_hundredth_rounds_up(self):
        # Три посещения из 32: 9.375 -> 9.38.
        rows = self.query(scopes=[(STUDENT, GROUP, 32)],
                          schedule=[dict(id=sc, group_id=GROUP) for sc in SCHEDULES],
                          schedule_scope=[(GROUP, sc) for sc in SCHEDULES],
                          marks=[self.mark(sc) for sc in SCHEDULES])
        self.assertEqual(rows[0]['attendance_percentage'], Decimal('9.38'))
        self.assertEqual(float(rows[0]['attendance_percentage']), round(3 / 32 * 100, 2))

    def test_standard_round_is_used_at_filter_boundary(self):
        ids = [f'50000000-0000-0000-0000-{index:012d}' for index in range(1, 24)]
        rows = self.query(scopes=[(STUDENT, GROUP, 160)],
                          schedule=[dict(id=sc, group_id=GROUP) for sc in ids],
                          schedule_scope=[(GROUP, sc) for sc in ids],
                          marks=[self.mark(sc) for sc in ids], lower=14.38, upper=14.38)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['attendance_percentage'], Decimal('14.38'))

    def test_order_total_uuid_filter_and_limit(self):
        third = '20000000-0000-0000-0000-000000000003'
        scopes = [(OTHER_STUDENT, GROUP, 4), (STUDENT, GROUP, 4), (third, GROUP, 2)]
        rows = self.query(scopes=scopes, limit=2)
        self.assertEqual([str(r['student_id']) for r in rows], [STUDENT, OTHER_STUDENT])
        rows = self.query(scopes=scopes, marks=[self.mark()], upper=0, limit=1)
        self.assertEqual([str(r['student_id']) for r in rows], [OTHER_STUDENT])
        self.assertEqual(self.query(scopes=[(STUDENT, GROUP, 0)]), [])

    def test_missing_metadata_does_not_refill_limit(self):
        scopes = [(STUDENT, OTHER_GROUP, 4), (OTHER_STUDENT, GROUP, 2)]
        rows = self.query(scopes=scopes, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]['student_id']), STUDENT)
        self.assertIsNone(rows[0]['metadata_group_id'])
        rows = self.query(specialities=[])
        self.assertIsNone(rows[0]['metadata_group_id'])


if __name__ == '__main__':
    unittest.main()
