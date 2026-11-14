import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from Labs import lab2 as lab


PATH = '/api/v1/labs/lab2/audience-report'
COURSE = '10000000-0000-0000-0000-000000000001'
OTHER_COURSE = '10000000-0000-0000-0000-000000000002'
LECTURE = '20000000-0000-0000-0000-000000000001'
OTHER_LECTURE = '20000000-0000-0000-0000-000000000002'
UNSCHEDULED = '20000000-0000-0000-0000-000000000003'
DEPARTMENT = '30000000-0000-0000-0000-000000000001'
OTHER_DEPARTMENT = '30000000-0000-0000-0000-000000000002'
UNIVERSITY = '40000000-0000-0000-0000-000000000001'


def lecture_row(lecture_id=LECTURE, course_id=COURSE, department_id=DEPARTMENT):
    return dict(course_id=course_id, course_name='Машинное обучение',
                description='Описание курса', semester=2, department_id=department_id,
                duration_hours=108, lecture_hours=54, practice_hours=54,
                speciality_id='50000000-0000-0000-0000-000000000001',
                speciality_name='Системный анализ', speciality_code='09.03.01',
                lecture_id=lecture_id, lecture_title='Название лекции',
                annotation='Аннотация', lecture_type='Lecture', technical_requirements=True)


def graph_row(lecture_id=LECTURE, group_id='group-1', schedules=1, listeners=5):
    return dict(course_id=COURSE, lecture_id=lecture_id, group_id=group_id,
                scheduled_count=schedules, listeners_count=listeners)


class AudienceReportTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.side_effect = [[lecture_row()]]
        self.pg = self.patch(lab, 'get_pg_conn', return_value=self.connection)
        self.driver = MagicMock()
        self.session = self.driver.session.return_value.__enter__.return_value
        self.session.run.return_value = [graph_row()]
        self.graph = self.patch(lab.GraphDatabase, 'driver', return_value=self.driver)
        self.driver.__enter__.return_value = self.driver
        self.mongo = MagicMock()
        self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy'].find.return_value = [
            dict(university_id=UNIVERSITY, name='Университет', institutes=[
                dict(institute_id='institute', name='Mongo институт', departments=[
                    dict(department_id=DEPARTMENT, name='Mongo кафедра')])])
        ]
        self.mongo_constructor = self.patch(lab, 'MongoClient', return_value=self.mongo)
        self.client = TestClient(lab.app)
        self.addCleanup(self.client.close)
        self.params = dict(semester=2, enrollment_year=2023)

    def patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def request_report(self):
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_unscheduled_lectures_and_courses_are_omitted(self):
        self.cursor.fetchall.side_effect = [
            [lecture_row(), lecture_row(UNSCHEDULED),
             lecture_row(OTHER_LECTURE, OTHER_COURSE, OTHER_DEPARTMENT)],
        ]
        reports = self.request_report()
        self.assertEqual(len(reports), 1)
        course = reports[0]
        self.assertEqual(course['course_id'], COURSE)
        self.assertEqual([item['lecture_id'] for item in course['lectures']], [LECTURE])
        self.assertEqual(course['total_enrolled_students'], 5)
        self.assertEqual(course['description'], 'Описание курса')
        self.assertEqual(course['university_name'], 'Университет')
        self.assertEqual(course['lectures'][0]['annotation'], 'Аннотация')
        self.assertEqual(course['lectures'][0]['scheduled_classes_count'], 1)
        self.assertNotIn('total_listeners_count', course['lectures'][0])
        self.assertEqual(course['lectures'][0]['required_room_capacity'], 5)
        self.assertEqual(self.cursor.execute.call_count, 1)
        self.assertEqual(course['department_name'], 'Mongo кафедра')
        self.assertEqual(course['institute_name'], 'Mongo институт')
        self.assertEqual(course['university_id'], UNIVERSITY)
        collection = self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy']
        self.assertEqual(collection.find.call_args.args[0],
                         {'institutes.departments.department_id': {'$in': [DEPARTMENT]}})
        self.assertEqual(self.session.run.call_args.kwargs['enrollment_year'], 2023)

    def test_empty_graph_result_skips_course_metadata_and_mongo(self):
        self.session.run.return_value = []
        self.assertEqual(self.request_report(), [])
        self.pg.assert_called_once()
        self.cursor.execute.assert_called_once()
        self.mongo_constructor.assert_not_called()

    def test_repeated_classes_and_groups_do_not_inflate_listener_totals(self):
        self.cursor.fetchall.side_effect = [
            [lecture_row(), lecture_row(OTHER_LECTURE)]
        ]
        self.session.run.return_value = [
            graph_row(schedules=3, listeners=5),
            graph_row(group_id='group-2', schedules=2, listeners=7),
            graph_row(OTHER_LECTURE, schedules=4, listeners=5),
        ]
        course = self.request_report()[0]
        self.assertEqual(course['total_enrolled_students'], 12)
        lectures = {item['lecture_id']: item for item in course['lectures']}
        self.assertEqual(lectures[LECTURE]['scheduled_classes_count'], 5)
        self.assertNotIn('total_listeners_count', lectures[LECTURE])
        self.assertEqual(lectures[LECTURE]['required_room_capacity'], 12)
        self.assertEqual(lectures[OTHER_LECTURE]['scheduled_classes_count'], 4)
        self.assertNotIn('total_listeners_count', lectures[OTHER_LECTURE])
        self.assertEqual(lectures[OTHER_LECTURE]['required_room_capacity'], 5)

    def test_zero_listener_course_is_not_emitted(self):
        self.session.run.return_value = [graph_row(schedules=2, listeners=0)]
        self.assertEqual(self.request_report(), [])
        self.assertEqual(self.mongo_constructor.call_count, 0)

    def test_zero_schedule_course_skips_mongo(self):
        self.session.run.return_value = [graph_row(schedules=0, listeners=5)]
        self.assertEqual(self.request_report(), [])
        self.mongo_constructor.assert_not_called()

    def test_no_postgres_candidates_skips_graph_and_mongo(self):
        self.cursor.fetchall.side_effect = [[]]
        self.assertEqual(self.request_report(), [])
        self.graph.assert_not_called()
        self.mongo_constructor.assert_not_called()

    def test_missing_or_incomplete_hierarchy_returns_503(self):
        collection = self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy']
        for docs in ([], [dict(university_id=UNIVERSITY, name='Вуз', institutes=[
                dict(institute_id='institute', name='Институт', departments=[dict(department_id=DEPARTMENT)])])]):
            with self.subTest(docs=docs):
                self.cursor.fetchall.side_effect = [[lecture_row()]]
                collection.find.return_value = docs
                self.assertEqual(self.client.get(PATH, params=self.params).status_code, 503)

    def test_mongo_unavailable_returns_503(self):
        self.mongo.__enter__.return_value[lab.MONGO_DB]['university_hierarchy'].find.side_effect = RuntimeError('down')
        self.assertEqual(self.client.get(PATH, params=self.params).status_code, 503)

    def test_graph_failure_returns_503(self):
        self.session.run.side_effect = RuntimeError('test graph unavailable')
        response = self.client.get(PATH, params=self.params)
        self.assertEqual(response.status_code, 503)
        self.pg.assert_called_once()
        self.mongo_constructor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
