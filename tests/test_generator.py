import contextlib
import io
import random
import re
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

import generator as seed
import generator_undo as undo


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        random.seed(42)
        seed.fake.seed_instance(42)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.data = seed.generate_random_data()

    def test_hierarchy_exists_only_in_mongo_and_uuid_links_agree(self):
        data = self.data
        self.assertTrue({'universities', 'institutes', 'departments'}.isdisjoint(data))
        departments = set()
        for university in data['mongo_hierarchy']:
            UUID(university['university_id'])
            self.assertEqual(len(university['institutes']), seed.INSTITUTES_PER_UNI)
            for institute in university['institutes']:
                UUID(institute['institute_id'])
                self.assertEqual(len(institute['departments']), seed.DEPTS_PER_INST)
                for department in institute['departments']:
                    UUID(department['department_id'])
                    self.assertTrue(department['name'])
                    self.assertNotIn(department['department_id'], departments)
                    departments.add(department['department_id'])
        self.assertEqual(len(departments), seed.NUM_UNIVERSITIES * seed.INSTITUTES_PER_UNI * seed.DEPTS_PER_INST)
        for key, index in [('groups', 2), ('courses', 2), ('dept_specs', 1)]:
            self.assertEqual({row[index] for row in data[key]}, departments)

    def test_real_generation_schedule_attendance_and_graph_invariants(self):
        data = self.data
        groups = {row[0]: row for row in data['groups']}
        courses = {row[0]: row for row in data['courses']}
        lectures = {row['id']: row for row in data['lectures']}
        students = {row[0]: row for row in data['students']}
        schedules = {row['id']: row for row in data['schedules']}
        self.assertEqual(len(students), len(groups) * seed.STUDENTS_PER_GROUP)
        self.assertEqual(len(lectures), len(courses) * seed.LECTURES_PER_COURSE)
        slots = {(s['group_id'], s['date'], s['period']) for s in schedules.values()}
        self.assertEqual(len(slots), len(schedules))
        for schedule in schedules.values():
            course = courses[lectures[schedule['class_id']]['course_id']]
            self.assertEqual(groups[schedule['group_id']][2], course[2])
        for attendance in data['attendances']:
            self.assertEqual(students[attendance[2]][1], schedules[attendance[1]]['group_id'])
        nodes = {props['id'] for _, props in data['neo_nodes']}
        for source, target, _ in data['neo_relations']:
            self.assertIn(source, nodes)
            self.assertIn(target, nodes)
        self.assertEqual({m['lecture_id'] for m in data['course_materials']}, set(lectures))

    def test_graph_course_and_lecture_properties_match_relational_source(self):
        specialities = {row[0]: row for row in self.data['specialities']}
        courses = {row[0]: row for row in self.data['courses']}
        lectures = {row['id']: row for row in self.data['lectures']}
        for label, props in self.data['neo_nodes']:
            if label == 'Course':
                row = courses[props['id']]
                spec = specialities[row[1]]
                expected = dict(id=row[0], speciality_id=row[1], department_id=row[2], name=row[3],
                                description=row[4], semester=row[5], duration_hours=row[6],
                                lecture_hours=row[7], practice_hours=row[8],
                                speciality_name=spec[1], speciality_code=spec[2])
                self.assertEqual(props, expected)
            elif label == 'Lecture':
                row = lectures[props['id']]
                self.assertEqual(props, {key: row[key] for key in
                                        ('id', 'title', 'type', 'annotation', 'technical_requirements')})

    def test_pg_ddl_has_external_department_uuids_not_hierarchy_tables(self):
        cursor = MagicMock()
        seed.init_pg_tables(cursor)
        sql = '\n'.join(call.args[0] for call in cursor.execute.call_args_list)
        for table in ('universities', 'institutes', 'departments'):
            self.assertNotRegex(sql, rf'(?i)(?:CREATE TABLE IF NOT EXISTS|REFERENCES) {table}\b')
        self.assertEqual(len(re.findall(r'department_id UUID NOT NULL\s*,', sql)), 3)

    def test_seeder_uses_undo_once_before_schema_without_independent_resets(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(seed, 'generate_random_data', return_value=self.data))
            reset = stack.enter_context(patch.object(seed, 'run_undo'))
            pg = stack.enter_context(patch.object(seed.psycopg2, 'connect'))
            values = stack.enter_context(patch.object(seed, 'execute_values'))
            neo = stack.enter_context(patch.object(seed.GraphDatabase, 'driver'))
            mongo = stack.enter_context(patch.object(seed, 'MongoClient'))
            es = stack.enter_context(patch.object(seed, 'Elasticsearch'))
            stack.enter_context(patch.object(seed.helpers, 'bulk'))
            cursor = pg.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.execute.side_effect = lambda *_: reset.assert_called_once_with()
            with contextlib.redirect_stdout(io.StringIO()):
                seed.run_seeder()
            reset.assert_called_once_with()
            sql = '\n'.join(call.args[0] for call in cursor.execute.call_args_list)
            self.assertNotIn('DROP TABLE', sql)
            inserts = '\n'.join(call.args[1] for call in values.call_args_list)
            for table in ('universities', 'institutes', 'departments'):
                self.assertNotIn('INSERT INTO ' + table, inserts)
            neo_sql = ' '.join(call.args[0] for call in neo.return_value.__enter__.return_value.session.return_value.__enter__.return_value.run.call_args_list)
            self.assertNotIn('DELETE', neo_sql)
            db = mongo.return_value.__enter__.return_value[seed.MONGO_DB]
            db.drop_collection.assert_not_called()
            db['university_hierarchy'].delete_many.assert_not_called()
            es.return_value.__enter__.return_value.indices.delete.assert_not_called()

    def test_undo_failure_prevents_seed_writes(self):
        with patch.object(seed, 'run_undo', side_effect=RuntimeError('cleanup failed')), \
                patch.object(seed.psycopg2, 'connect') as connect, \
                patch.object(seed, 'generate_random_data', return_value=self.data), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                seed.run_seeder()
            connect.assert_not_called()


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.pg = self.stack.enter_context(patch.object(undo.psycopg2, 'connect'))
        self.neo = self.stack.enter_context(patch.object(undo.GraphDatabase, 'driver'))
        self.redis = self.stack.enter_context(patch.object(undo.redis, 'Redis'))
        self.mongo = self.stack.enter_context(patch.object(undo, 'MongoClient'))
        self.es = self.stack.enter_context(patch.object(undo, 'Elasticsearch'))
        self.conn = self.pg.return_value
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (True,)
        self.cursor.fetchall.return_value = [('10000000-0000-0000-0000-000000000001',)]
        self.cache = self.redis.return_value.__enter__.return_value
        self.session = self.neo.return_value.__enter__.return_value.session.return_value.__enter__.return_value
        self.db = self.mongo.return_value.__enter__.return_value[undo.MONGO_DB]
        collections = {name: MagicMock() for name in ('university_hierarchy', 'course_materials')}
        self.db.__getitem__.side_effect = collections.__getitem__
        self.search = self.es.return_value.__enter__.return_value

    def test_cleanup_only_project_artifacts_and_raw_student_keys(self):
        undo.run_undo()
        self.assertEqual(undo.PG_DB, seed.PG_DB)
        sqls = [call.args[0] for call in self.cursor.execute.call_args_list]
        select = next(i for i, sql in enumerate(sqls) if 'SELECT id' in sql)
        drop = next(i for i, sql in enumerate(sqls) if 'DROP TABLE' in sql)
        self.assertLess(select, drop)
        self.assertNotIn('CASCADE', sqls[drop])
        self.assertEqual(set(re.findall(r'public\.([a-z_]+)', sqls[drop])),
                         {'attendance', 'schedule', 'course_materials', 'lectures', 'classes', 'courses',
                          'students', 'groups', 'department_specialties', 'specialities', 'departments', 'institutes', 'universities'})
        self.cache.delete.assert_called_once_with('10000000-0000-0000-0000-000000000001')
        self.cache.keys.assert_not_called()
        self.cache.flushall.assert_not_called()
        self.db['university_hierarchy'].drop.assert_called_once_with()
        self.db['course_materials'].drop.assert_not_called()
        self.search.indices.delete.assert_called_once_with(index='course_materials')
        cypher = ' '.join(call.args[0] for call in self.session.run.call_args_list)
        self.assertNotIn('CONSTRAINT', cypher)
        self.assertIn('n:Student', cypher)
        for label in ('Group', 'Course', 'Lecture', 'Schedule'):
            self.assertIn('n:' + label, cypher)
        self.assertNotIn('MATCH (n) DETACH DELETE', cypher)
        self.assertLessEqual(self.pg.call_args.kwargs['connect_timeout'], 5)
        self.assertLessEqual(self.redis.call_args.kwargs['socket_connect_timeout'], 5)
        self.assertLessEqual(self.mongo.call_args.kwargs['serverSelectionTimeoutMS'], 5000)
        self.assertLessEqual(self.es.call_args.kwargs['request_timeout'], 5)
        self.conn.close.assert_called_once_with()

    def test_preflight_failure_does_not_delete_and_hides_credentials(self):
        self.search.info.side_effect = RuntimeError('password=DO_NOT_LEAK')
        with self.assertRaises(RuntimeError) as error:
            undo.run_undo()
        self.assertNotIn('DO_NOT_LEAK', str(error.exception))
        self.assertNotIn('DO_NOT_LEAK', self.output.getvalue())
        self.assertFalse(any('DROP TABLE' in c.args[0] for c in self.cursor.execute.call_args_list))
        self.cache.delete.assert_not_called()
        self.db['university_hierarchy'].drop.assert_not_called()
        self.search.indices.delete.assert_not_called()

    def test_partial_failure_is_not_reported_as_success(self):
        self.db['university_hierarchy'].drop.side_effect = RuntimeError('password=DO_NOT_LEAK')
        with self.assertRaises(RuntimeError) as error:
            undo.run_undo()
        self.assertNotIn('DO_NOT_LEAK', str(error.exception))
        self.assertNotIn('завершена', self.output.getvalue())
        self.search.indices.delete.assert_not_called()

    def test_missing_students_table_and_index_are_safe_to_repeat(self):
        self.cursor.fetchone.return_value = (False,)
        self.search.indices.exists.return_value = False
        undo.run_undo()
        self.cache.delete.assert_not_called()
        self.search.indices.delete.assert_not_called()

    def test_every_preflight_failure_stops_before_first_delete(self):
        checks = [self.pg, self.neo.return_value.__enter__.return_value.verify_connectivity,
                  self.cache.ping, self.mongo.return_value.__enter__.return_value.admin.command,
                  self.search.info]
        for check in checks:
            with self.subTest(check=check._mock_name):
                check.side_effect = RuntimeError('DO_NOT_LEAK')
                try:
                    with self.assertRaises(RuntimeError) as error:
                        undo.run_undo()
                    self.assertNotIn('DO_NOT_LEAK', str(error.exception))
                    self.assertFalse(any('DROP TABLE' in c.args[0] for c in self.cursor.execute.call_args_list))
                    self.cache.delete.assert_not_called()
                    self.session.run.assert_not_called()
                    self.db['university_hierarchy'].drop.assert_not_called()
                    self.search.indices.delete.assert_not_called()
                finally:
                    check.side_effect = None

    def test_redis_failure_preserves_pg_student_ids_for_retry(self):
        self.cache.delete.side_effect = RuntimeError('DO_NOT_LEAK')
        with self.assertRaises(RuntimeError):
            undo.run_undo()
        self.assertFalse(any('DROP TABLE' in c.args[0] for c in self.cursor.execute.call_args_list))
        self.session.run.assert_not_called()

    def test_cli_exits_nonzero_on_mocked_connection_failure_without_secrets(self):
        # Подменяем драйвер до запуска скрипта, чтобы тест не подключался к настоящим БД.
        result = subprocess.run([sys.executable, '-c',
            "import runpy; from unittest.mock import patch; "
            "p=patch('psycopg2.connect', side_effect=RuntimeError('DO_NOT_LEAK')); p.start(); "
            "runpy.run_path('generator_undo.py', run_name='__main__')"],
            cwd=seed.os.path.dirname(seed.__file__), capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('DO_NOT_LEAK', result.stderr + result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertTrue(result.stderr.strip())


if __name__ == '__main__':
    unittest.main()
