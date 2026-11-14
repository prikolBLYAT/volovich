import json
import logging
import os
from contextlib import closing
from datetime import date
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from neo4j import GraphDatabase
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient
import redis


env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)
DEFAULT_HOST = os.getenv('DB_HOST', 'localhost')

PG_HOST = os.getenv('POSTGRES_HOST') or DEFAULT_HOST
PG_PORT = os.getenv('POSTGRES_PORT', '5432')
PG_USER = os.getenv('POSTGRES_USER', 'admin')
PG_PASS = os.getenv('POSTGRES_PASSWORD')
PG_DB = os.getenv('POSTGRES_DB', 'postgres')

NEO4J_HOST = os.getenv('NEO4J_HOST') or DEFAULT_HOST
NEO4J_BOLT_PORT = os.getenv('NEO4J_BOLT_PORT', '7687')
NEO4J_USER, _, NEO4J_PASS = os.getenv('NEO4J_AUTH', 'neo4j/').partition('/')

MONGO_HOST = os.getenv('MONGO_HOST') or DEFAULT_HOST
MONGO_PORT = os.getenv('MONGO_PORT', '27017')
MONGO_USER = os.getenv('MONGO_INITDB_ROOT_USERNAME', 'admin')
MONGO_PASS = os.getenv('MONGO_INITDB_ROOT_PASSWORD')
MONGO_DB = os.getenv('MONGO_INITDB_DATABASE', 'db')

REDIS_HOST = os.getenv('REDIS_HOST') or DEFAULT_HOST
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_PASS = os.getenv('REDIS_PASSWORD')
redis_client = redis.Redis(
    host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASS,
    decode_responses=True, socket_connect_timeout=3, socket_timeout=3,
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title='Лабораторная работа №3',
    description='Запланированные и посещённые часы лекций по студентам всех групп и курсам их кафедр.',
    version='1.0.0',
)


class GroupInfoDTO(BaseModel):
    group_id: str
    group_name: str
    speciality_id: str
    department_id: str
    enrollment_year: int
    curator: Optional[str]
    speciality_name: str
    speciality_code: str
    degree_level: Optional[str]
    duration: Optional[int]
    department_name: Optional[str]
    institute_id: Optional[str] = None
    institute_name: Optional[str] = None
    university_id: Optional[str] = None
    university_name: Optional[str] = None


class CourseHoursDTO(BaseModel):
    course_id: str
    speciality_id: str
    department_id: str
    course_name: str
    description: Optional[str]
    semester: int
    duration_hours: Optional[int]
    lecture_hours: Optional[int]
    practice_hours: Optional[int]
    planned_classes: int
    attended_classes: int
    planned_hours: int
    attended_hours: int


class StudentHoursDTO(BaseModel):
    student_id: str
    group_id: str
    first_name: str
    second_name: str
    email: Optional[str]
    phone: Optional[str]
    student_card_number: str
    status: Optional[str]
    courses: List[CourseHoursDTO]


class GroupHoursReportDTO(BaseModel):
    group: GroupInfoDTO
    period_start: date
    period_end: date
    academic_hours_per_lecture: int = 2
    students: List[StudentHoursDTO]


def get_pg_conn():
    try:
        return psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
            dbname=PG_DB, connect_timeout=5,
        )
    except psycopg2.Error:
        raise HTTPException(status_code=503, detail='PostgreSQL недоступен')


def get_student_profiles(student_groups):
    """Формат кэша тот же, что в первой лабе: UUID студента и его данные на сутки."""
    if not student_groups:
        return {}
    student_ids = list(student_groups)
    profiles = {}
    required_fields = {
        'student_id', 'group_id', 'first_name', 'second_name', 'email', 'phone',
        'student_card_number', 'status',
    }
    try:
        # Читаем студентов одним запросом. Записи другой группы и неполные данные из кэша не используем.
        for student_id, raw in zip(student_ids, redis_client.mget(student_ids)):
            if raw is None:
                continue
            try:
                profile = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if (isinstance(profile, dict) and required_fields.issubset(profile)
                    and profile['student_id'] == student_id and profile['group_id'] == student_groups[student_id]):
                profiles[student_id] = profile
    except redis.RedisError as exc:
        logger.warning('Чтение кэша студентов: %s', type(exc).__name__)

    missing_ids = [student_id for student_id in student_ids if student_id not in profiles]
    if missing_ids:
        with closing(get_pg_conn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''
                    SELECT id AS student_id, group_id, first_name, second_name,
                           email, phone, student_card_number, status
                    FROM students
                    WHERE (id, group_id) IN (
                        SELECT * FROM unnest(%s::uuid[], %s::uuid[])
                    );
                ''', (missing_ids, [student_groups[sid] for sid in missing_ids]))
                fetched = {
                    str(row['student_id']): {
                        **dict(row), 'student_id': str(row['student_id']), 'group_id': str(row['group_id']),
                    }
                    for row in cur.fetchall()
                }
        if (set(fetched) != set(missing_ids)
                or any(row['group_id'] != student_groups[sid] for sid, row in fetched.items())):
            raise HTTPException(status_code=503, detail='Состав группы в PostgreSQL и Neo4j не согласован')
        profiles.update(fetched)
        try:
            with redis_client.pipeline(transaction=False) as pipeline:
                for student_id, profile in fetched.items():
                    # Ключ кэша совпадает с UUID студента, срок хранения сутки.
                    pipeline.setex(student_id, 86400, json.dumps(profile, ensure_ascii=False))
                # Отправляем накопленные записи в Redis за один вызов.
                pipeline.execute()
        except redis.RedisError as exc:
            logger.warning('Запись кэша студентов: %s', type(exc).__name__)
    return profiles


@app.get(
    '/api/v1/labs/lab3/hours-report', response_model=List[GroupHoursReportDTO],
    tags=['Лабораторная работа №3'], summary='Плановые и посещённые часы лекций по всем группам',
)
def get_hours_report(
    period_start: date = Query(..., description='Начало периода, включительно'),
    period_end: date = Query(..., description='Конец периода, включительно'),
):
    if period_start > period_end:
        raise HTTPException(status_code=400, detail='period_start не может быть позже period_end')

    # Названия подразделений читаем только из MongoDB.
    with closing(get_pg_conn()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('''
                SELECT g.id AS group_id, g.name AS group_name, g.speciality_id, g.department_id,
                       g.enrollment_year, g.curator, sp.name AS speciality_name,
                       sp.code AS speciality_code, sp.degree_level, sp.duration
                FROM groups g
                JOIN specialities sp ON sp.id = g.speciality_id
                ORDER BY g.id;
            ''')
            group_rows = cur.fetchall()
    groups = {
        str(row['group_id']): GroupInfoDTO(**{
            **dict(row), 'group_id': str(row['group_id']), 'department_name': None,
            'department_id': str(row['department_id']), 'speciality_id': str(row['speciality_id']),
        }) for row in group_rows
    }
    if not groups:
        return []

    # Собираем состав до расписания, чтобы студенты не размножали строки занятий.
    try:
        with GraphDatabase.driver(
            f'bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}', auth=(NEO4J_USER, NEO4J_PASS),
        ) as driver:
            with driver.session() as session:
                scopes = session.run('''
                    MATCH (g:Group)
                    WHERE g.id IN $group_ids
                    OPTIONAL MATCH (st:Student)-[:BELONGS_TO]->(g)
                    WITH g, collect(DISTINCT st.id) AS student_ids
                    OPTIONAL MATCH (g)-[:HAS_SCHEDULE]->(sc:Schedule)
                                   -[:FOR_LECTURE]->(l:Lecture)-[:OF_COURSE]->(c:Course)
                    WHERE sc.date >= $period_start AND sc.date <= $period_end
                      AND l.type = 'Lecture'
                    WITH g, student_ids, c.id AS course_id, collect(DISTINCT sc.id) AS schedule_ids
                    RETURN g.id AS group_id, student_ids,
                           collect(CASE WHEN course_id IS NULL THEN null
                           ELSE {course_id: course_id, schedule_ids: schedule_ids} END) AS courses
                ''', group_ids=list(groups), period_start=str(period_start), period_end=str(period_end)).data()
    except Exception:
        raise HTTPException(status_code=503, detail='Neo4j недоступен')
    if {scope['group_id'] for scope in scopes} != set(groups):
        raise HTTPException(status_code=503, detail='Группа отсутствует в Neo4j')

    students_by_group = {}
    schedules_by_group = {}
    student_groups = {}
    for scope in scopes:
        group_id = scope['group_id']
        students_by_group[group_id] = sorted(set(scope['student_ids']))
        schedules_by_group[group_id] = {
            item['course_id']: sorted(set(item['schedule_ids'])) for item in scope['courses']
        }
        for student_id in students_by_group[group_id]:
            if student_id in student_groups and student_groups[student_id] != group_id:
                raise HTTPException(status_code=503, detail='Студент принадлежит нескольким группам в Neo4j')
            student_groups[student_id] = group_id
    student_groups = dict(sorted(student_groups.items()))

    # Передаём точные пары, иначе курс чужой кафедры может попасть в другую группу.
    candidate_pairs = sorted({
        (course_id, groups[group_id].department_id)
        for group_id, courses in schedules_by_group.items() for course_id in courses
    })
    courses_by_group = {group_id: [] for group_id in groups}
    attendance = {}
    if candidate_pairs:
        with closing(get_pg_conn()) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('''
                    SELECT id AS course_id, speciality_id, department_id, name AS course_name,
                           description, semester, duration_hours, lecture_hours, practice_hours
                    FROM courses
                    WHERE (id, department_id) IN (
                        SELECT * FROM unnest(%s::uuid[], %s::uuid[])
                    )
                    ORDER BY semester, name, id;
                ''', ([pair[0] for pair in candidate_pairs], [pair[1] for pair in candidate_pairs]))
                course_rows = cur.fetchall()
                for group_id, group in groups.items():
                    courses_by_group[group_id] = [
                        row for row in course_rows if str(row['department_id']) == group.department_id
                        and str(row['course_id']) in schedules_by_group[group_id]
                    ]
                selected_scope = [
                    (schedule_id, str(course['course_id']), group_id)
                    for group_id, courses in courses_by_group.items() for course in courses
                    for schedule_id in schedules_by_group[group_id][str(course['course_id'])]
                ]
                if selected_scope and student_groups:
                    cur.execute('''
                        WITH selected_schedule AS (
                            SELECT * FROM unnest(%s::uuid[], %s::uuid[], %s::uuid[])
                                AS scope(schedule_id, course_id, group_id)
                        )
                        SELECT sc.group_id, a.student_id, sc.course_id,
                               COUNT(DISTINCT a.schedule_id) AS attended_classes
                        FROM selected_schedule sc
                        JOIN attendance a ON a.schedule_id = sc.schedule_id
                        JOIN students st ON st.id = a.student_id AND st.group_id = sc.group_id
                        WHERE a.student_id = ANY(%s::uuid[])
                          AND a.date BETWEEN %s AND %s AND a.mark = 1
                        GROUP BY sc.group_id, a.student_id, sc.course_id;
                    ''', ([item[0] for item in selected_scope], [item[1] for item in selected_scope],
                          [item[2] for item in selected_scope], list(student_groups), period_start, period_end))
                    attendance = {
                        (str(row['group_id']), str(row['student_id']), str(row['course_id'])): row['attended_classes']
                        for row in cur.fetchall()
                    }

    profiles = get_student_profiles(student_groups)
    department_ids = sorted({group.department_id for group in groups.values()})
    hierarchy = {}
    try:
        with MongoClient(
            host=MONGO_HOST, port=int(MONGO_PORT), username=MONGO_USER,
            password=MONGO_PASS, authSource='admin', serverSelectionTimeoutMS=3000,
        ) as client:
            documents = client[MONGO_DB]['university_hierarchy'].find(
                {'institutes.departments.department_id': {'$in': department_ids}},
                {'_id': 0, 'university_id': 1, 'name': 1, 'institutes': 1},
            )
            for university in documents:
                for institute in university.get('institutes', []):
                    for department in institute.get('departments', []):
                        if department.get('department_id') in department_ids:
                            hierarchy[department['department_id']] = dict(
                                department_name=department.get('name'),
                                institute_id=institute.get('institute_id'), institute_name=institute.get('name'),
                                university_id=university.get('university_id'), university_name=university.get('name'),
                            )
    except Exception:
        raise HTTPException(status_code=503, detail='MongoDB недоступна')
    for group in groups.values():
        fields = hierarchy.get(group.department_id)
        if not fields or not all(fields.values()):
            raise HTTPException(status_code=503, detail='Иерархия кафедры в MongoDB неполна')
        for name, value in fields.items():
            setattr(group, name, value)

    reports = []
    for group_id, group in groups.items():
        students = []
        for student_id in students_by_group[group_id]:
            course_hours = []
            for course in courses_by_group[group_id]:
                course_id = str(course['course_id'])
                planned = len(schedules_by_group[group_id][course_id])
                attended = attendance.get((group_id, student_id, course_id), 0)
                course_hours.append(CourseHoursDTO(
                    **{**dict(course), 'course_id': course_id,
                       'speciality_id': str(course['speciality_id']), 'department_id': str(course['department_id'])},
                    planned_classes=planned, attended_classes=attended,
                    planned_hours=planned * 2, attended_hours=attended * 2,
                ))
            students.append(StudentHoursDTO(**profiles[student_id], courses=course_hours))
        reports.append(GroupHoursReportDTO(
            group=group, period_start=period_start, period_end=period_end, students=students,
        ))
    return reports
