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
GROUP2 = '10000000-0000-0000-0000-000000000002'
DEPT2 = '20000000-0000-0000-0000-000000000002'
DEPT = '20000000-0000-0000-0000-000000000001'
SPEC = '30000000-0000-0000-0000-000000000001'
STUDENT = '40000000-0000-0000-0000-000000000001'
STUDENT2 = '40000000-0000-0000-0000-000000000002'
COURSE = '50000000-0000-0000-0000-000000000001'
SCHEDULE = '60000000-0000-0000-0000-000000000001'
SCHEDULE2 = '60000000-0000-0000-0000-000000000002'
PATH = '/api/v1/labs/lab3/hours-report'


def profile(student_id=STUDENT, group_id=GROUP):
    return dict(student_id=student_id, group_id=group_id, first_name='Иван',
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
        self.assertEqual(lab.get_student_profiles({STUDENT: GROUP}), {STUDENT: profile()})
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
        result = lab.get_student_profiles({STUDENT: GROUP, STUDENT2: GROUP})
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
        self.assertIn('unnest(%s::uuid[], %s::uuid[])', sql)
        self.assertEqual(params, ([STUDENT, STUDENT2], [GROUP, GROUP]))

    def test_mixed_cache_fetches_and_caches_only_misses(self):
        self.redis.mget.return_value = [json.dumps(profile()), None]
        self.cursor.fetchall.return_value = [profile(STUDENT2)]
        result = lab.get_student_profiles({STUDENT: GROUP, STUDENT2: GROUP})
        self.assertEqual(set(result), {STUDENT, STUDENT2})
        self.assertEqual(self.cursor.execute.call_args.args[1], ([STUDENT2], [GROUP]))
        self.assertEqual(self.pipeline.setex.call_args.args[:2], (STUDENT2, 86400))

    def test_invalid_cached_json_and_identity_are_cache_misses(self):
        invalid = ['{', 'null', '[]', '{}', json.dumps({**profile(), 'student_id': STUDENT2}),
                   json.dumps({**profile(), 'group_id': STUDENT2})]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.redis.mget.return_value = [raw]
                self.cursor.fetchall.return_value = [profile()]
                self.assertEqual(lab.get_student_profiles({STUDENT: GROUP}), {STUDENT: profile()})
        self.assertEqual(self.pg.call_count, len(invalid))

    def test_redis_read_down_falls_back_to_pg(self):
        self.redis.mget.side_effect = lab.redis.ConnectionError('test unavailable')
        self.cursor.fetchall.return_value = [profile()]
        with self.assertLogs(lab.logger, level='WARNING'):
            self.assertEqual(lab.get_student_profiles({STUDENT: GROUP}), {STUDENT: profile()})

    def test_redis_write_down_does_not_lose_pg_profile(self):
        self.redis.mget.return_value = [None]
        self.cursor.fetchall.return_value = [profile()]
        self.pipeline.execute.side_effect = lab.redis.ConnectionError('test unavailable')
        with self.assertLogs(lab.logger, level='WARNING'):
            self.assertEqual(lab.get_student_profiles({STUDENT: GROUP}), {STUDENT: profile()})

    def test_missing_pg_profile_reports_inconsistent_roster(self):
        self.redis.mget.return_value = [None]
        self.cursor.fetchall.return_value = []
        with self.assertRaises(HTTPException) as error:
            lab.get_student_profiles({STUDENT: GROUP})
        self.assertEqual(error.exception.status_code, 503)
        self.redis.pipeline.assert_not_called()

    def test_empty_roster_does_not_query_services(self):
        self.assertEqual(lab.get_student_profiles({}), {})
        self.redis.mget.assert_not_called()
        self.pg.assert_not_called()

    def test_multi_group_cache_validates_each_expected_group_and_batches_misses(self):
        expected = {STUDENT: GROUP, STUDENT2: GROUP2}
        self.redis.mget.return_value = [json.dumps(profile(STUDENT, GROUP2)), json.dumps(profile(STUDENT2, GROUP))]
        rows = [profile(STUDENT), profile(STUDENT2, GROUP2)]
        self.cursor.fetchall.return_value = rows
        self.assertEqual(lab.get_student_profiles(expected), {row['student_id']: row for row in rows})
        self.redis.mget.assert_called_once_with([STUDENT, STUDENT2])
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.assertEqual(self.cursor.execute.call_args.args[1], ([STUDENT, STUDENT2], [GROUP, GROUP2]))
        self.pipeline.execute.assert_called_once_with()

    def test_pg_profile_with_wrong_group_or_identity_returns_503_without_cache_write(self):
        for row in [profile(STUDENT, GROUP2), profile(STUDENT2)]:
            with self.subTest(row=row):
                self.redis.mget.return_value = [None]
                self.cursor.fetchall.return_value = [row]
                with self.assertRaises(HTTPException) as error:
                    lab.get_student_profiles({STUDENT: GROUP})
                self.assertEqual(error.exception.status_code, 503)
        self.redis.pipeline.assert_not_called()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(lab.app)
        self.addCleanup(self.client.close)
        self.params = dict(period_start='2026-09-01', period_end='2026-09-30')
        self.groups = [group_row()]
        self.courses = [course_row()]
        self.attendance = []
        self.profile_rows = [profile()]
        self.executed = []
        self.cursor = MagicMock()
        self.cursor.execute.side_effect = self.execute
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = self.cursor
        self.pg = self.start_patch('get_pg_conn', return_value=connection)
        self.neo = self.start_patch('GraphDatabase')
        session = self.neo.driver.return_value.__enter__.return_value.session.return_value.__enter__.return_value
        self.neo_run = session.run
        self.scope: dict = dict(group_id=GROUP, student_ids=[STUDENT], courses=[dict(course_id=COURSE, schedule_ids=[SCHEDULE])])
        self.scopes = [self.scope]
        self.neo_run.return_value.data.side_effect = self.graph_data
        self.redis = self.start_patch('redis_client')
        self.redis.mget.side_effect = lambda ids: [json.dumps(profile(student_id)) for student_id in ids]
        self.mongo = self.start_patch('MongoClient')
        collection = self.mongo.return_value.__enter__.return_value.__getitem__.return_value.__getitem__.return_value
        self.collection = collection
        collection.find.return_value = [dict(university_id='university', name='Вуз', institutes=[
            dict(institute_id='other', name='Чужой институт', departments=[dict(department_id=STUDENT2)]),
            dict(institute_id='institute', name='Институт', departments=[dict(department_id=DEPT, name='Mongo кафедра')])])]

    def start_patch(self, name, **kwargs):
        patcher = patch.object(lab, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def graph_data(self):
        name = self.neo_run.call_args.kwargs.get('group_name')
        matching = {row['group_id'] for row in self.groups if name is None or row['group_name'] == name}
        return [scope for scope in self.scopes if name is None or scope['group_id'] in matching]

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if 'FROM groups g' in sql:
            self.cursor.fetchall.return_value = [row for row in self.groups if row['group_id'] in params[0]]
        elif 'FROM courses' in sql:
            self.cursor.fetchall.return_value = self.courses
        elif 'JOIN attendance' in sql:
            self.cursor.fetchall.return_value = self.attendance
        elif 'FROM students' in sql:
            self.cursor.fetchall.return_value = self.profile_rows
        else:
            raise AssertionError('Неожиданный SQL в изолированном тесте')

    def get(self, **overrides):
        return self.client.get(PATH, params={**self.params, **overrides})

    def test_neo4j_runs_before_postgres_and_pg_reads_share_connection(self):
        self.neo_run.side_effect = lambda *args, **kwargs: (
            self.pg.assert_not_called() or self.neo_run.return_value)
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.pg.assert_called_once()

    def test_empty_graph_skips_every_other_database(self):
        self.scopes = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        self.pg.assert_not_called()
        self.redis.mget.assert_not_called()
        self.mongo.assert_not_called()

    def test_schedule_duplicates_count_once_and_zero_attendance_is_retained(self):
        self.scope['courses'][0]['schedule_ids'] = [SCHEDULE2, SCHEDULE, SCHEDULE]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()[0]
        self.assertEqual(body['group']['institute_name'], 'Институт')
        self.assertEqual(body['group']['university_name'], 'Вуз')
        course = body['courses'][0]
        self.assertEqual((course['planned_classes'], course['planned_hours']), (2, 4))
        self.assertEqual(body['students'][0]['attendance'][0], dict(course_id=COURSE, attended_classes=0, attended_hours=0))
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
        self.assertEqual(response.json()[0]['group']['department_name'], 'Mongo кафедра')
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
        self.assertEqual([item['student_id'] for item in response.json()[0]['students']], [STUDENT, STUDENT2])
        self.redis.mget.assert_called_once_with([STUDENT, STUDENT2])

    def test_attended_hours_are_counted_per_student_and_course(self):
        self.scope['student_ids'] = [STUDENT, STUDENT2]
        self.attendance = [dict(group_id=UUID(GROUP), student_id=UUID(STUDENT), course_id=UUID(COURSE), attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        students = response.json()[0]['students']
        self.assertEqual(students[0]['attendance'][0]['attended_hours'], 2)
        self.assertEqual(students[1]['attendance'][0]['attended_hours'], 0)

    def test_empty_period_retains_students_with_empty_attendance(self):
        self.scope['courses'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        students = response.json()[0]['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['attendance'], [])
        self.assertEqual(len(self.executed), 1)

    def test_empty_roster_returns_empty_students(self):
        self.scope['student_ids'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]['students'], [])
        self.redis.mget.assert_not_called()
        self.assertFalse(any('JOIN attendance' in sql for sql, _ in self.executed))

    def test_reversed_period_is_400_before_services(self):
        response = self.get(period_start='2026-10-01')
        self.assertEqual(response.status_code, 400)
        self.pg.assert_not_called()
        self.neo.driver.assert_not_called()

    def test_no_groups_returns_empty_list_before_other_services(self):
        self.scopes = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        self.pg.assert_not_called()
        self.mongo.assert_not_called()
        self.redis.mget.assert_not_called()

    def test_group_id_is_not_an_api_input(self):
        parameters = self.client.get('/openapi.json').json()['paths'][PATH]['get']['parameters']
        self.assertEqual({item['name'] for item in parameters}, {'period_start', 'period_end', 'group_name'})
        self.assertEqual(self.get(group_id='not-a-uuid').status_code, 200)

    def test_missing_pg_group_returns_503(self):
        self.groups = []
        self.assertEqual(self.get().status_code, 503)

    def test_neo_unavailable_returns_503(self):
        self.neo_run.side_effect = RuntimeError('down')
        self.assertEqual(self.get().status_code, 503)

    def test_invalid_or_missing_query_parameters_are_422(self):
        for overrides in [dict(period_start='bad-date'), dict(period_end='2026-02-30')]:
            with self.subTest(overrides=overrides):
                self.assertEqual(self.get(**overrides).status_code, 422)
        self.assertEqual(self.client.get(PATH).status_code, 422)
        self.pg.assert_not_called()

    def add_second_group(self):
        self.groups.append({**group_row(), 'group_id': GROUP2, 'department_id': DEPT2})
        self.scopes.append(dict(group_id=GROUP2, student_ids=[STUDENT2], courses=[
            dict(course_id=COURSE, schedule_ids=[SCHEDULE2])]))
        self.redis.mget.side_effect = lambda ids: [
            json.dumps(profile(sid, GROUP2 if sid == STUDENT2 else GROUP)) for sid in ids]
        self.collection.find.return_value[0]['institutes'][1]['departments'].append(
            dict(department_id=DEPT2, name='Другая кафедра'))

    def test_multiple_groups_isolate_courses_and_batch_all_reads(self):
        self.add_second_group()
        self.scope['courses'][0]['schedule_ids'] = [SCHEDULE, SCHEDULE2]
        self.attendance = [dict(group_id=GROUP, student_id=STUDENT, course_id=COURSE, attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        first, second = response.json()
        self.assertEqual((first['group']['group_id'], second['group']['group_id']), (GROUP, GROUP2))
        self.assertEqual(first['courses'][0]['planned_hours'], 4)
        self.assertEqual(first['students'][0]['attendance'][0]['attended_hours'], 2)
        self.assertEqual(second['students'][0]['group_id'], GROUP2)
        self.assertEqual(second['students'][0]['attendance'], [])
        self.assertEqual(len(self.executed), 3)
        self.neo_run.assert_called_once()
        self.assertNotIn('group_ids', self.neo_run.call_args.kwargs)
        self.redis.mget.assert_called_once_with([STUDENT, STUDENT2])
        self.collection.find.assert_called_once_with(
            {'institutes.departments.department_id': {'$in': [DEPT, DEPT2]}},
            {'_id': 0, 'university_id': 1, 'name': 1, 'institutes': 1})
        sql, params = next(item for item in self.executed if 'FROM courses' in item[0])
        self.assertEqual(set(zip(*params)), {(COURSE, DEPT), (COURSE, DEPT2)})

    def test_multi_group_cache_misses_use_four_pg_queries_and_one_pipeline(self):
        self.add_second_group()
        self.redis.mget.side_effect = lambda ids: [None] * len(ids)
        self.profile_rows = [profile(), profile(STUDENT2, GROUP2)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 2)
        self.assertEqual(len(self.executed), 4)
        self.assertEqual(self.pg.call_count, 2)
        self.neo_run.assert_called_once()
        self.redis.mget.assert_called_once_with([STUDENT, STUDENT2])
        self.redis.pipeline.assert_called_once_with(transaction=False)
        pipeline = self.redis.pipeline.return_value.__enter__.return_value
        self.assertEqual(pipeline.setex.call_count, 2)
        pipeline.execute.assert_called_once_with()
        self.collection.find.assert_called_once()

    def test_different_departments_receive_only_their_own_scheduled_courses(self):
        self.add_second_group()
        other_course = '50000000-0000-0000-0000-000000000002'
        self.scopes[1]['courses'].append(dict(course_id=other_course, schedule_ids=[SCHEDULE2]))
        self.courses.append(course_row(other_course, DEPT2, STUDENT2))
        self.attendance = [dict(group_id=GROUP2, student_id=STUDENT2,
                               course_id=other_course, attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        courses = [group['courses'] for group in response.json()]
        self.assertEqual([[c['course_id'] for c in rows] for rows in courses], [[COURSE], [other_course]])
        self.assertEqual(response.json()[1]['students'][0]['attendance'][0]['attended_hours'], 2)
        self.assertEqual(len(self.executed), 3)

    def test_multiple_courses_keep_attendance_separate(self):
        other_course = '50000000-0000-0000-0000-000000000002'
        self.scope['courses'].append(dict(course_id=other_course, schedule_ids=[SCHEDULE2]))
        self.courses.append(course_row(other_course))
        self.attendance = [dict(group_id=GROUP, student_id=STUDENT, course_id=other_course, attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        courses = response.json()[0]['students'][0]['attendance']
        self.assertEqual([(c['course_id'], c['attended_hours']) for c in courses], [(COURSE, 0), (other_course, 2)])

    def test_same_department_shared_course_keeps_group_hours_separate(self):
        self.add_second_group()
        self.groups[1]['department_id'] = DEPT
        self.scope['courses'][0]['schedule_ids'] = [SCHEDULE, SCHEDULE2]
        self.attendance = [
            dict(group_id=GROUP2, student_id=STUDENT2, course_id=COURSE, attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([group['courses'][0]['planned_hours'] for group in response.json()], [4, 2])
        self.assertEqual([group['students'][0]['attendance'][0]['attended_hours'] for group in response.json()], [0, 2])
        self.assertEqual(len(self.executed), 3)

    def test_other_group_missing_neo_or_mongo_is_not_silently_dropped(self):
        self.add_second_group()
        group = self.groups.pop()
        self.assertEqual(self.get().status_code, 503)
        self.groups.append(group)
        self.collection.find.return_value[0]['institutes'][1]['departments'].pop()
        self.assertEqual(self.get().status_code, 503)

    def test_other_group_incomplete_mongo_returns_503(self):
        self.add_second_group()
        del self.collection.find.return_value[0]['institutes'][1]['departments'][1]['name']
        self.assertEqual(self.get().status_code, 503)

    def test_student_in_multiple_groups_returns_503(self):
        self.add_second_group()
        self.scopes[1]['student_ids'] = [STUDENT]
        self.assertEqual(self.get().status_code, 503)

    def test_all_groups_empty_period_and_empty_group_are_retained(self):
        self.add_second_group()
        for scope in self.scopes:
            scope['courses'] = []
        self.scopes[1]['student_ids'] = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        first, second = response.json()
        self.assertEqual(first['students'][0]['attendance'], [])
        self.assertEqual(second['students'], [])
        self.assertEqual(len(self.executed), 1)

    def test_normalized_contract_keeps_course_metadata_once_and_two_attendance_rows(self):
        self.scope['student_ids'] = [STUDENT, STUDENT2]
        self.attendance = [dict(group_id=GROUP, student_id=STUDENT, course_id=COURSE, attended_classes=1)]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()[0]
        self.assertEqual(len(report['courses']), 1)
        self.assertEqual(set(report['courses'][0]), set(course_row()) | {'planned_classes', 'planned_hours'})
        self.assertEqual(report['courses'][0]['planned_hours'], 2)
        self.assertEqual(len(report['students']), 2)
        for student, hours in zip(report['students'], [2, 0]):
            self.assertEqual(set(student), set(profile()) | {'attendance'})
            self.assertEqual(student['attendance'], [dict(course_id=COURSE, attended_classes=hours // 2, attended_hours=hours)])

    def test_empty_roster_still_has_shared_course_plan(self):
        self.scope['student_ids'] = []
        report = self.get().json()[0]
        self.assertEqual(report['students'], [])
        self.assertEqual(report['courses'], [{**course_row(), 'planned_classes': 1, 'planned_hours': 2}])

    def test_group_name_exact_binding_preserves_quotes_case_and_significant_spaces(self):
        for name in ['Группа', "Группа' OR 1=1 --", '  Группа  ']:
            with self.subTest(name=name):
                self.groups[0]['group_name'] = name
                self.executed.clear()
                response = self.get(group_name=name)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()), 1)
                cypher = self.neo_run.call_args.args[0]
                self.assertEqual(self.neo_run.call_args.kwargs['group_name'], name)
                self.assertIn('WHERE g.name = $group_name', cypher)
                self.assertNotIn(name, cypher)
                self.assertEqual(self.executed[0][1], ([GROUP],))
        self.assertEqual(self.get(group_name='группа').status_code, 404)
        self.assertEqual(self.get(group_name='Группа').status_code, 404)

    def test_unknown_group_name_is_404_before_downstream_services(self):
        response = self.get(group_name='Неизвестная')
        self.assertEqual(response.status_code, 404)
        self.pg.assert_not_called()
        self.neo_run.assert_called_once()
        self.mongo.assert_not_called()
        self.redis.mget.assert_not_called()

    def test_blank_group_name_is_422_before_any_database(self):
        for name in ['', ' ', '\t\n', '\u2003']:
            with self.subTest(name=name):
                self.assertEqual(self.get(group_name=name).status_code, 422)
        self.pg.assert_not_called()
        self.neo.driver.assert_not_called()
        self.mongo.assert_not_called()
        self.redis.mget.assert_not_called()

    def test_duplicate_group_names_return_all_matches_as_list(self):
        self.add_second_group()
        response = self.get(group_name='Группа')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r['group']['group_id'] for r in response.json()], [GROUP, GROUP2])
        self.assertNotIn('group_ids', self.neo_run.call_args.kwargs)

    def test_filter_limits_graph_sql_redis_and_mongo_to_selected_scope(self):
        self.add_second_group()
        self.groups[1]['group_name'] = 'Другая'
        self.neo_run.return_value.data.side_effect = lambda: [self.scopes[1]]
        self.courses = [course_row(department_id=DEPT2)]
        response = self.get(group_name='Другая')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r['group']['group_id'] for r in response.json()], [GROUP2])
        self.assertEqual(self.neo_run.call_args.kwargs['group_name'], 'Другая')
        self.assertEqual(self.executed[0][1], ([GROUP2],))
        self.redis.mget.assert_called_once_with([STUDENT2])
        course_params = next(params for sql, params in self.executed if 'FROM courses' in sql)
        self.assertEqual(course_params, ([COURSE], [DEPT2]))
        attendance_params = next(params for sql, params in self.executed if 'JOIN attendance' in sql)
        self.assertEqual(attendance_params[:4], ([SCHEDULE2], [COURSE], [GROUP2], [STUDENT2]))
        self.assertEqual(self.collection.find.call_args.args[0],
                         {'institutes.departments.department_id': {'$in': [DEPT2]}})

    def test_no_filter_has_no_optional_sql_predicate(self):
        self.add_second_group()
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 2)
        cypher = self.neo_run.call_args.args[0]
        self.assertNotIn('WHERE g.name', cypher)
        self.assertNotIn('group_name', self.neo_run.call_args.kwargs)
        self.assertEqual(self.executed[0][1], ([GROUP, GROUP2],))

    def test_actual_candidate_sql_filters_exact_pairs_not_speciality_or_technical_flag(self):
        self.add_second_group()
        foreign = '50000000-0000-0000-0000-000000000002'
        self.scopes[1]['courses'] = [dict(course_id=foreign, schedule_ids=[SCHEDULE2])]
        self.assertEqual(self.get().status_code, 200)
        sql, params = next(item for item in self.executed if 'FROM courses' in item[0])
        # SQLite заменяет только табличный параметр PostgreSQL.
        sql = sql.replace('SELECT * FROM unnest(%s::uuid[], %s::uuid[])', 'VALUES (?, ?), (?, ?)')
        self.assertNotIn('%s', sql)
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE courses (id TEXT, speciality_id TEXT, department_id TEXT, '
                       'name TEXT, description TEXT, semester INTEGER, duration_hours INTEGER, '
                       'lecture_hours INTEGER, practice_hours INTEGER)')
            db.execute('CREATE TABLE lectures (course_id TEXT, technical_requirements INTEGER)')
            rows = [(COURSE, SPEC, DEPT), (foreign, STUDENT, DEPT2), (foreign, SPEC, DEPT)]
            for index, row in enumerate(rows):
                db.execute('INSERT INTO courses VALUES (?, ?, ?, ?, NULL, 1, 72, 36, 36)', (*row, 'Курс'))
                db.execute('INSERT INTO lectures VALUES (?, ?)', (row[0], [0, 1, None][index]))
            selected = {(row[0], row[2]) for row in db.execute(sql, [v for pair in zip(*params) for v in pair])}
        self.assertEqual(selected, {(COURSE, DEPT), (foreign, DEPT2)})

    def test_actual_attendance_sql_enforces_schedule_course_group_student_date_and_mark(self):
        self.add_second_group()
        self.groups[1]['department_id'] = DEPT
        before, after, absent = [f'60000000-0000-0000-0000-{n:012d}' for n in (3, 4, 5)]
        self.scopes[1]['courses'][0]['schedule_ids'].extend([before, after, absent])
        self.assertEqual(self.get().status_code, 200)
        sql, params = next(item for item in self.executed if 'JOIN attendance' in item[0])
        # SQLite заменяет массивы параметров, сохраняя соединения и условия запроса.
        placeholders = ', '.join('(?, ?, ?)' for _ in params[0])
        sql = re.sub(r'SELECT \* FROM unnest\(%s::uuid\[\], %s::uuid\[\], %s::uuid\[\]\)\s*AS scope\(schedule_id, course_id, group_id\)',
                     'SELECT column1 AS schedule_id, column2 AS course_id, column3 AS group_id '
                     f'FROM (VALUES {placeholders})', sql)
        sql = sql.replace('a.student_id = ANY(%s::uuid[])', 'a.student_id IN (?, ?)').replace('%s', '?')
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE students (id TEXT, group_id TEXT)')
            db.executemany('INSERT INTO students VALUES (?, ?)', [(STUDENT, GROUP), (STUDENT2, GROUP2)])
            db.execute('CREATE TABLE attendance (student_id TEXT, schedule_id TEXT, date TEXT, mark INTEGER)')
            db.executemany('INSERT INTO attendance VALUES (?, ?, ?, ?)', [
                (STUDENT, SCHEDULE, '2026-09-01', 1), (STUDENT, SCHEDULE, '2026-09-01', 1),
                (STUDENT2, SCHEDULE2, '2026-09-30', 1), (STUDENT2, SCHEDULE, '2026-09-05', 1),
                (STUDENT, SCHEDULE2, '2026-09-05', 1), (STUDENT2, after, '2026-10-01', 1),
                (STUDENT2, before, '2026-08-31', 1), (STUDENT2, absent, '2026-09-10', 0),
                (STUDENT, STUDENT, '2026-09-02', 1)])
            values = [v for triple in zip(*params[:3]) for v in triple] + list(params[3])
            values.extend(value.isoformat() for value in params[4:])
            result = db.execute(sql, values)
            rows = [dict(zip([column[0] for column in result.description], row)) for row in result]
        self.assertEqual({(r['group_id'], r['student_id'], r['course_id'], r['attended_classes']) for r in rows},
                         {(GROUP, STUDENT, COURSE, 1), (GROUP2, STUDENT2, COURSE, 1)})


if __name__ == '__main__':
    unittest.main()
