import json
from contextlib import closing
import re
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient

from Labs import lab3 as lab


GROUP = '10000000-0000-0000-0000-000000000001'
DEPT = '20000000-0000-0000-0000-000000000001'
SPEC = '30000000-0000-0000-0000-000000000001'
STUDENT = '40000000-0000-0000-0000-000000000001'
STUDENT2 = '40000000-0000-0000-0000-000000000002'
COURSE = '50000000-0000-0000-0000-000000000001'
SCHEDULE = '60000000-0000-0000-0000-000000000001'
SCHEDULE2 = '60000000-0000-0000-0000-000000000002'
PATH = '/api/v1/labs/lab3/hours-report'


def profile(student_id=STUDENT):
    return dict(student_id=student_id, group_id=GROUP, first_name='Иван',
                second_name='Иванов', email=None, phone=None,
                student_card_number=student_id[-8:], status='active')


def group_row():
    return dict(group_id=GROUP, group_name='Группа', speciality_id=SPEC,
                department_id=DEPT, enrollment_year=2026, curator=None,
                speciality_name='Специальность', speciality_code='09.03.01',
                degree_level='Бакалавриат', duration=4)


def course_row(course_id=COURSE, department_id=DEPT, speciality_id=SPEC):
    return dict(course_id=course_id, speciality_id=speciality_id,
                department_id=department_id, course_name='Курс', description=None,
                semester=1, duration_hours=72, lecture_hours=36, practice_hours=36)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.redis = MagicMock()
        self.redis.mget.return_value = []
        self.pipeline = self.redis.pipeline.return_value.__enter__.return_value
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.pg_patch = patch.object(lab, 'get_pg_conn', return_value=self.connection)
        self.pg = self.pg_patch.start()
        self.redis_patch = patch.object(lab, 'redis_client', self.redis)
        self.redis_patch.start()
        self.addCleanup(self.pg_patch.stop)
        self.addCleanup(self.redis_patch.stop)

    def test_valid_json_cache_hit_uses_raw_uuid_and_skips_pg(self):
        self.redis.mget.return_value = [json.dumps(profile(), ensure_ascii=False)]
        self.assertEqual(lab.get_student_profiles([STUDENT], GROUP), {STUDENT: profile()})
        self.redis.mget.assert_called_once_with([STUDENT])
        self.pg.assert_not_called()
        self.redis.pipeline.assert_not_called()

    def test_all_missing_profiles_are_pipelined_with_raw_keys_and_day_ttl(self):
        self.redis.mget.return_value = [None, None]
        rows = [profile(STUDENT), profile(STUDENT2)]
        self.cursor.fetchall.return_value = [
            {**row, 'student_id': UUID(row['student_id']), 'group_id': UUID(GROUP)}
            for row in rows
        ]
        result = lab.get_student_profiles([STUDENT, STUDENT2], GROUP)
        self.assertEqual(result, {row['student_id']: row for row in rows})
        self.redis.pipeline.assert_called_once_with(transaction=False)
        self.assertEqual(self.pipeline.setex.call_count, 2)
        self.pipeline.execute.assert_called_once_with()
        cached = {}
        for call in self.pipeline.setex.call_args_list:
            key, ttl, value = call.args
            self.assertEqual(ttl, 86400)
            self.assertIn(key, [STUDENT, STUDENT2])
            cached[key] = json.loads(value)
        self.assertEqual(cached, result)
        sql, params = self.cursor.execute.call_args.args
        self.assertIn('group_id = %s::uuid', sql)
        self.assertEqual(params, ([STUDENT, STUDENT2], GROUP))

    def test_mixed_cache_fetches_and_caches_only_misses(self):
        self.redis.mget.return_value = [json.dumps(profile()), None]
        self.cursor.fetchall.return_value = [profile(STUDENT2)]
        result = lab.get_student_profiles([STUDENT, STUDENT2], GROUP)
        self.assertEqual(set(result), {STUDENT, STUDENT2})
        self.assertEqual(self.cursor.execute.call_args.args[1], ([STUDENT2], GROUP))
        self.assertEqual(self.pipeline.setex.call_args.args[:2], (STUDENT2, 86400))

    def test_invalid_cached_json_and_identity_are_cache_misses(self):
        invalid = ['{', 'null', '[]', '{}', json.dumps({**profile(), 'student_id': STUDENT2}),
                   json.dumps({**profile(), 'group_id': STUDENT2})]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.redis.mget.return_value = [raw]
                self.cursor.fetchall.return_value = [profile()]
                self.assertEqual(lab.get_student_profiles([STUDENT], GROUP), {STUDENT: profile()})
        self.assertEqual(self.pg.call_count, len(invalid))

    def test_redis_read_down_falls_back_to_pg(self):
        self.redis.mget.side_effect = lab.redis.ConnectionError('test unavailable')
        self.cursor.fetchall.return_value = [profile()]
        with self.assertLogs(lab.logger, level='WARNING'):
            self.assertEqual(lab.get_student_profiles([STUDENT], GROUP), {STUDENT: profile()})

    def test_redis_write_down_does_not_lose_pg_profile(self):
        self.redis.mget.return_value = [None]
        self.cursor.fetchall.return_value = [profile()]
        self.pipeline.execute.side_effect = lab.redis.ConnectionError('test unavailable')
        with self.assertLogs(lab.logger, level='WARNING'):
            self.assertEqual(lab.get_student_profiles([STUDENT], GROUP), {STUDENT: profile()})

    def test_missing_pg_profile_reports_inconsistent_roster(self):
        self.redis.mget.return_value = [None]
        self.cursor.fetchall.return_value = []
        with self.assertRaises(HTTPException) as error:
            lab.get_student_profiles([STUDENT], GROUP)
        self.assertEqual(error.exception.status_code, 503)
        self.redis.pipeline.assert_not_called()

    def test_empty_roster_does_not_query_services(self):
        self.assertEqual(lab.get_student_profiles([], GROUP), {})
        self.redis.mget.assert_not_called()
        self.pg.assert_not_called()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(lab.app)
        self.addCleanup(self.client.close)
        self.params = dict(group_id=GROUP, period_start='2026-09-01', period_end='2026-09-30')
        self.group = group_row()
        self.courses = [course_row()]
        self.attendance = []
        self.executed = []
        self.cursor = MagicMock()
        self.cursor.execute.side_effect = self.execute
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = self.cursor
        self.pg = self.start_patch('get_pg_conn', return_value=connection)
        self.neo = self.start_patch('GraphDatabase')
        session = self.neo.driver.return_value.__enter__.return_value.session.return_value.__enter__.return_value
        self.neo_run = session.run
        self.scope = dict(student_ids=[STUDENT], courses=[dict(course_id=COURSE, schedule_ids=[SCHEDULE])])
        self.neo_run.return_value.single.side_effect = lambda: self.scope
        self.redis = self.start_patch('redis_client')
        self.redis.mget.side_effect = lambda ids: [json.dumps(profile(student_id)) for student_id in ids]
        self.mongo = self.start_patch('MongoClient')
        collection = self.mongo.return_value.__enter__.return_value.__getitem__.return_value.__getitem__.return_value
        collection.find.return_value = [dict(university_id='university', name='Вуз', institutes=[
            dict(institute_id='other', name='Чужой институт', departments=[dict(department_id=STUDENT2)]),
            dict(institute_id='institute', name='Институт', departments=[dict(department_id=DEPT, name='Mongo кафедра')])])]

    def start_patch(self, name, **kwargs):
        patcher = patch.object(lab, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def execute(self, sql, params):
        self.executed.append((sql, params))
        if 'FROM groups g' in sql:
            self.cursor.fetchone.return_value = self.group
        elif 'FROM courses' in sql:
            self.cursor.fetchall.return_value = self.courses
        elif 'JOIN attendance' in sql:
            self.cursor.fetchall.return_value = self.attendance
        else:
            raise AssertionError('Unexpected PostgreSQL query in isolated test')

    def get(self, **overrides):
        return self.client.get(PATH, params={**self.params, **overrides})

    def test_schedule_duplicates_count_once_and_zero_attendance_is_retained(self):
        self.scope['courses'][0]['schedule_ids'] = [SCHEDULE2, SCHEDULE, SCHEDULE]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['group']['institute_name'], 'Институт')
        self.assertEqual(body['group']['university_name'], 'Вуз')
        course = body['students'][0]['courses'][0]
        self.assertEqual((course['planned_classes'], course['planned_hours']), (2, 4))
        self.assertEqual((course['attended_classes'], course['attended_hours']), (0, 0))
        sql, params = next(item for item in self.executed if 'JOIN attendance' in item[0])
        self.assertIn('COUNT(DISTINCT a.schedule_id)', sql)
        self.assertIn('a.date BETWEEN %s AND %s', sql)
        self.assertIn('a.mark = 1', sql)
        self.assertEqual(params[0], [SCHEDULE, SCHEDULE2])
        cypher = self.neo_run.call_args.args[0]
        self.assertIn('collect(DISTINCT st.id)', cypher)
        self.assertIn('collect(DISTINCT sc.id)', cypher)
        self.assertLess(cypher.index('WITH g,'), cypher.index('HAS_SCHEDULE'))

    def test_mongo_department_and_pg_query_have_no_duplicate_hierarchy(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['group']['department_name'], 'Mongo кафедра')
        for sql, _ in self.executed:
            for table in ('departments', 'institutes', 'universities'):
                self.assertNotIn(table, sql.lower())

    def test_missing_or_incomplete_hierarchy_returns_503(self):
        collection = self.mongo.return_value.__enter__.return_value[lab.MONGO_DB]['university_hierarchy']
        for docs in ([], [dict(university_id='uni', name='Вуз', institutes=[
                dict(institute_id='inst', name='Институт', departments=[dict(department_id=DEPT)])])]):
            with self.subTest(docs=docs):
                collection.find.return_value = docs
                self.assertEqual(self.get().status_code, 503)

    def test_mongo_unavailable_returns_503(self):
        self.mongo.side_effect = RuntimeError('down')
        self.assertEqual(self.get().status_code, 503)

    def test_student_ids_from_scope_are_unique(self):
        self.scope['student_ids'] = [STUDENT, STUDENT, STUDENT2]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item['student_id'] for item in response.json()['students']], [STUDENT, STUDENT2])
        self.redis.mget.assert_called_once_with([STUDENT, STUDENT2])

    def test_attended_hours_are_counted_per_student_and_course(self):
        self.scope['student_ids'] = [STUDENT, STUDENT2]
        self.attendance = [dict(student_id=UUID(STUDENT), course_id=UUID(COURSE), attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        students = response.json()['students']
        self.assertEqual(students[0]['courses'][0]['attended_hours'], 2)
        self.assertEqual(students[1]['courses'][0]['attended_hours'], 0)

    def test_empty_period_retains_students_with_empty_courses(self):
        self.scope['courses'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        students = response.json()['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['courses'], [])
        self.assertEqual(len(self.executed), 1)

    def test_empty_roster_returns_empty_students(self):
        self.scope['student_ids'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['students'], [])
        self.redis.mget.assert_not_called()
        self.assertFalse(any('JOIN attendance' in sql for sql, _ in self.executed))

    def test_reversed_period_is_400_before_services(self):
        response = self.get(period_start='2026-10-01')
        self.assertEqual(response.status_code, 400)
        self.pg.assert_not_called()
        self.neo.driver.assert_not_called()

    def test_unknown_group_is_404_before_other_services(self):
        self.group = None
        self.assertEqual(self.get().status_code, 404)
        self.neo.driver.assert_not_called()
        self.mongo.assert_not_called()

    def test_invalid_or_missing_query_parameters_are_422(self):
        for overrides in [dict(group_id='bad-uuid'), dict(period_start='bad-date'), dict(period_end='2026-02-30')]:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.get(**overrides).status_code, 422)
        self.assertEqual(self.client.get(PATH).status_code, 422)
        self.pg.assert_not_called()

    def test_actual_candidate_sql_filters_only_department_not_speciality_or_technical_flag(self):
        self.assertEqual(self.get().status_code, 200)
        sql = next(sql for sql, _ in self.executed if 'FROM courses' in sql)
        ids = [f'50000000-0000-0000-0000-{index:012d}' for index in range(1, 5)]
        # Проверяем запрос на курсах разных кафедр и специальностей.
        # Для SQLite меняем только запись параметров. Требования к оборудованию на отбор не влияют.
        sql = sql.replace('id = ANY(%s::uuid[])', 'id IN (?, ?, ?, ?)')
        sql = sql.replace('%s::uuid', '?')
        self.assertNotIn('%s', sql)
        with closing(sqlite3.connect(':memory:')) as db:
            # Создаём в памяти таблицу с нужными полями курсов.
            db.execute('CREATE TABLE courses (id TEXT, speciality_id TEXT, department_id TEXT, '
                       'name TEXT, description TEXT, semester INTEGER, duration_hours INTEGER, '
                       'lecture_hours INTEGER, practice_hours INTEGER)')
            # Добавляем таблицу лекций с требованиями к оборудованию.
            db.execute('CREATE TABLE lectures (course_id TEXT, technical_requirements INTEGER)')
            for index, candidate_id in enumerate(ids):
                dept = DEPT if index < 3 else STUDENT2
                spec = SPEC if index == 0 else STUDENT2
                # Добавляем курсы разных кафедр и специальностей.
                db.execute('INSERT INTO courses VALUES (?, ?, ?, ?, NULL, 1, 72, 36, 36)',
                           (candidate_id, spec, dept, 'Курс'))
                # У лекций будут разные требования к оборудованию.
                db.execute('INSERT INTO lectures VALUES (?, ?)', (candidate_id, [0, 1, None, 1][index]))
            # Выполняем запрос из обработчика на подготовленных данных.
            selected = {row[0] for row in db.execute(sql, (*ids, DEPT))}
        self.assertEqual(selected, set(ids[:3]))


if __name__ == '__main__':
    unittest.main()
