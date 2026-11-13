import json
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
        self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy'].find.return_value = []
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
            dict(student_id=STUDENT, group_id=GROUP,
                 schedule_ids=SCHEDULES[:2], lecture_ids=[LECTURE]),
            dict(student_id=OTHER_STUDENT, group_id=OTHER_GROUP,
                 schedule_ids=SCHEDULES[2:], lecture_ids=[LECTURE]),
        ]

    def group_metadata(self):
        return dict(group_id=GROUP, department_id=DEPARTMENT, group_name='Группа',
                    speciality_name='Информатика', speciality_code='09.03.01',
                    degree_level='Бакалавриат', department_name='Кафедра')

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
        self.cursor.fetchall.side_effect = [
            [dict(student_id=STUDENT, attended_classes=1),
             dict(student_id=OTHER_STUDENT, attended_classes=1)],
            [self.group_metadata()],
        ]
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
        self.assertEqual(self.events, ['neo4j', 'postgres', 'postgres'])
        self.assertEqual(self.pg.call_count, 2)
        graph_params = self.session.run.call_args.kwargs
        self.assertEqual(graph_params, dict(lecture_ids=[LECTURE],
                                           p_start='2026-09-01', p_end='2026-10-01'))
        attendance, metadata = self.cursor.execute.call_args_list
        self.assertEqual(set(attendance.args[1][0]), set(SCHEDULES))
        self.assertEqual(set(attendance.args[1][1]), {STUDENT, OTHER_STUDENT})
        self.assertIn('WHERE g.id = ANY(%s::uuid[])', metadata.args[0])
        self.assertEqual(metadata.args[1], ([GROUP],))
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

    def test_cold_cache_fetches_profile_after_group_metadata(self):
        self.graph_rows = [dict(student_id=STUDENT, group_id=GROUP,
                                schedule_ids=SCHEDULES[:1], lecture_ids=[LECTURE])]
        self.cache.mget.return_value = [None]
        self.cursor.fetchall.side_effect = [[], [self.group_metadata()], [profile()]]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        student = response.json()['students'][0]
        self.assertEqual(student['student_id'], STUDENT)
        self.assertEqual(student['attendance_percentage'], 0.0)
        self.assertEqual(self.events, ['neo4j', 'postgres', 'postgres', 'postgres'])
        self.assertEqual(self.cursor.execute.call_args.args[1], ([STUDENT],))
        key, ttl, value = self.cache.setex.call_args.args
        self.assertEqual((key, ttl), (STUDENT, 86400))
        self.assertEqual(json.loads(value), profile())

    def test_stale_cached_group_does_not_override_graph_membership(self):
        self.graph_rows = [dict(student_id=STUDENT, group_id=GROUP,
                                schedule_ids=SCHEDULES[:1], lecture_ids=[LECTURE])]
        self.cache.mget.return_value = [json.dumps({**profile(), 'group_id': OTHER_GROUP})]
        self.cursor.fetchall.side_effect = [[], [self.group_metadata()]]
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        students = response.json()['students']
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]['group_name'], 'Группа')
        self.assertEqual(self.cursor.execute.call_args.args[1], ([GROUP],))


if __name__ == '__main__':
    unittest.main()
