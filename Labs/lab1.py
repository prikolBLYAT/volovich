import os
import json
from contextlib import closing
from math import gcd
from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase
from pymongo import MongoClient
import redis
from dotenv import load_dotenv

# При локальном запуске берём .env из корня проекта.
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

DEFAULT_HOST = os.getenv("DB_HOST", "localhost")

PG_HOST = os.getenv("POSTGRES_HOST") or DEFAULT_HOST
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "postgres")

MONGO_HOST = os.getenv("MONGO_HOST") or DEFAULT_HOST
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD")
MONGO_DB = os.getenv("MONGO_INITDB_DATABASE", "db")

ES_HOST = os.getenv("ES_HOST") or DEFAULT_HOST
ES_PORT = os.getenv("ES_PORT", "9200")
ES_PASS = os.getenv("ELASTIC_PASSWORD", "superpassword123!")

NEO4J_HOST = os.getenv("NEO4J_HOST") or DEFAULT_HOST
NEO4J_BOLT_PORT = os.getenv("NEO4J_BOLT_PORT", "7687")
neo_auth = os.getenv("NEO4J_AUTH", "neo4j/superpassword123!").split('/')
NEO4J_USER = neo_auth[0]
NEO4J_PASS = neo_auth[1] if len(neo_auth) > 1 else "superpassword123!"

REDIS_HOST = os.getenv("REDIS_HOST") or DEFAULT_HOST
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASS = os.getenv("REDIS_PASSWORD", "superpassword123!")

app = FastAPI(
    title="Лабораторная работа №1",
    description="""
    ### Задание:
    Формирование отчета о 10 студентах с минимальным процентом посещения лекций, 
    содержащих заданный термин или фразу, за определенный период обучения.
    """,
    version="1.0.0",
    docs_url="/docs"
)

neo4j_driver = GraphDatabase.driver(
    f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}",
    auth=(NEO4J_USER, NEO4J_PASS),
    max_connection_pool_size=50
)

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=int(REDIS_PORT),
    password=REDIS_PASS,
    decode_responses=True,
    socket_timeout=3
)


# Формат ответа со сведениями о студенте.

class FullStudentInfoDTO(BaseModel):
    student_id: str
    first_name: str
    second_name: str
    email: str
    phone: str
    student_card_number: str
    status: str
    group_name: str
    speciality_name: str
    speciality_code: str
    degree_level: str
    department_name: str
    institute_name: str
    university_name: str
    total_classes: int
    attended_classes: int
    attendance_percentage: float


class ReportResponseDTO(BaseModel):
    term: str
    period_start: date
    period_end: date
    min_attendance_pct: float
    max_attendance_pct: float
    matching_lectures_count: int
    students: List[FullStudentInfoDTO]


# Подключения к базам данных.

def get_pg_conn():
    try:
        return psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB, connect_timeout=5
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PostgreSQL недоступен: {str(e)}; {PG_HOST}"
        )


def get_es_client():
    try:
        client = Elasticsearch([f"http://{ES_HOST}:{ES_PORT}"], basic_auth=("elastic", ES_PASS), request_timeout=5)
        return client
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Elasticsearch недоступен: {str(e)}; {ES_HOST}"
        )


def percentage_rounding_corrections(totals):
    # В точных половинах сохраняем прежний round Python, включая двоичную погрешность.
    corrections = {}
    for total in set(totals):
        if total <= 0:
            continue
        step = total // gcd(total, 10000)
        if step % 2:
            continue
        for attended in range(step // 2, total + 1, step):
            hundredths, _ = divmod(attended * 10000, total)
            exact = (hundredths + hundredths % 2) / 100
            previous = round(attended / total * 100, 2)
            if previous != exact:
                corrections[f"{total}:{attended}"] = previous
    return json.dumps(corrections)


ATTENDANCE_REPORT_SQL = """
WITH scoped_students AS (
    SELECT * FROM unnest(%s::uuid[], %s::uuid[], %s::int[])
        AS scope(student_id, group_id, total_classes)
), scoped_schedules AS (
    SELECT * FROM unnest(%s::uuid[], %s::uuid[]) AS scope(group_id, schedule_id)
), attended AS (
    SELECT ss.student_id, COUNT(DISTINCT a.schedule_id) AS attended_classes
    FROM scoped_students ss
    JOIN students st ON st.id = ss.student_id AND st.group_id = ss.group_id
    JOIN scoped_schedules gs ON gs.group_id = ss.group_id
    JOIN schedule sc ON sc.id = gs.schedule_id AND sc.group_id = ss.group_id
    JOIN attendance a ON a.student_id = ss.student_id AND a.schedule_id = sc.id
    WHERE a.date BETWEEN %s AND %s AND a.mark = 1
    GROUP BY ss.student_id
), counts AS (
    SELECT ss.*, COALESCE(a.attended_classes, 0) AS attended_classes
    FROM scoped_students ss LEFT JOIN attended a USING (student_id)
    WHERE ss.total_classes > 0
), percentages AS (
    SELECT *, COALESCE((%s::jsonb ->> (total_classes::text || ':' || attended_classes::text))::numeric,
        (div(attended_classes * 10000::numeric, total_classes) +
        CASE WHEN mod(attended_classes * 10000::numeric, total_classes) * 2 > total_classes
               OR (mod(attended_classes * 10000::numeric, total_classes) * 2 = total_classes
                   AND mod(div(attended_classes * 10000::numeric, total_classes), 2) = 1)
             THEN 1 ELSE 0 END) / 100) AS attendance_percentage
    FROM counts
), ranked AS (
    SELECT * FROM percentages WHERE attendance_percentage BETWEEN %s AND %s
    ORDER BY attendance_percentage ASC, total_classes DESC, student_id ASC
    LIMIT %s
)
SELECT r.*, g.id AS metadata_group_id, g.department_id, g.name AS group_name,
       sp.name AS speciality_name, sp.code AS speciality_code, sp.degree_level
FROM ranked r
LEFT JOIN (groups g JOIN specialities sp ON sp.id = g.speciality_id) ON g.id = r.group_id
ORDER BY r.attendance_percentage ASC, r.total_classes DESC, r.student_id ASC
"""


# Отчёт о посещаемости лекций.

@app.get(
    "/api/v1/labs/lab1/attendance-report",
    response_model=ReportResponseDTO,
    tags=["Лабораторная работа №1"],
    summary="Отчет о студентах с минимальной посещаемостью"
)
def get_attendance_report(
        term: str = Query(..., description="Термин/фраза для поиска в лекциях",
                          json_schema_extra={"example": "Базы данных"}),
        period_start: date = Query(..., description="Начало периода", json_schema_extra={"example": "2026-09-01"}),
        period_end: date = Query(..., description="Конец периода", json_schema_extra={"example": "2026-10-01"}),
        min_attendance_pct: float = Query(0.0, ge=0, le=100, description="Минимальный процент посещаемости (%)",
                                          json_schema_extra={"example": 0.0}),
        max_attendance_pct: float = Query(100.0, ge=0, le=100, description="Максимальный процент посещаемости (%)",
                                          json_schema_extra={"example": 100.0}),
        limit: int = Query(10, ge=1, le=100, description="Лимит топа студентов", json_schema_extra={"example": 10})
):
    # Проверяем порядок дат и границ посещаемости до обращения к БД.
    if period_start > period_end:
        raise HTTPException(status_code=400, detail="period_start не может быть позже period_end")
    if min_attendance_pct > max_attendance_pct:
        raise HTTPException(status_code=400, detail="min_attendance_pct не может быть больше max_attendance_pct")

    # Ищем материалы лекций по введённой фразе.
    es = get_es_client()
    try:
        # Из совпадений нужны только lecture_id. За один запрос берём до 10 000 материалов.
        es_res = es.search(
            index="course_materials",
            body={
                "query": {
                    "multi_match": {
                        "query": term,
                        "fields": ["title^3", "content_text"]
                    }
                },
                "_source": ["lecture_id"]
            },
            size=10000
        )
        hits = es_res["hits"]["hits"]
        lecture_ids = sorted({hit["_source"]["lecture_id"] for hit in hits})
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ошибка Elasticsearch: {str(e)}"
        )

    if not lecture_ids:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=0, students=[]
        )

    # По найденным лекциям ищем занятия за период, группы и студентов.
    student_schedules = {}
    group_schedules = {}
    scheduled_lecture_ids = set()
    try:
        with neo4j_driver.session() as session:
            cypher_query = """
            MATCH (l:Lecture)<-[:FOR_LECTURE]-(sc:Schedule)<-[:HAS_SCHEDULE]-(g:Group)
            WHERE sc.date >= $p_start AND sc.date <= $p_end
              AND l.id IN $lecture_ids
              AND l.type = 'Lecture'
            WITH g, collect(DISTINCT sc.id) AS schedule_ids,
                 collect(DISTINCT l.id) AS lecture_ids
            MATCH (st:Student)-[:BELONGS_TO]->(g)
            RETURN g.id AS group_id, schedule_ids, lecture_ids,
                   collect(DISTINCT st.id) AS student_ids
            """
            # Возвращаем занятия один раз на группу, вместе со списком её студентов.
            res = session.run(
                cypher_query,
                lecture_ids=lecture_ids,
                p_start=str(period_start),
                p_end=str(period_end)
            )
            for r in res:
                scheduled_lecture_ids.update(r["lecture_ids"])
                group_scope = {
                    "group_id": r["group_id"],
                    "schedule_ids": r["schedule_ids"]
                }
                group_schedules[r["group_id"]] = r["schedule_ids"]
                for student_id in r["student_ids"]:
                    student_schedules[student_id] = group_scope
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ошибка графового расчета в Neo4j: {str(e)}"
        )

    if not student_schedules:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=len(scheduled_lecture_ids), students=[]
        )

    # Передаём точные связи графа; занятия группы не размножаем по студентам.
    student_ids = list(student_schedules)
    schedule_groups = []
    schedule_ids = []
    for group_id, ids in group_schedules.items():
        schedule_groups.extend([group_id] * len(ids))
        schedule_ids.extend(ids)
    params = (
        student_ids,
        [student_schedules[s]["group_id"] for s in student_ids],
        [len(student_schedules[s]["schedule_ids"]) for s in student_ids],
        schedule_groups, schedule_ids, period_start, period_end,
        percentage_rounding_corrections(len(ids) for ids in group_schedules.values()),
        min_attendance_pct, max_attendance_pct, limit
    )
    with closing(get_pg_conn()) as pg_conn:
        with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(ATTENDANCE_REPORT_SQL, params)
            ranked_students = [dict(row, student_id=str(row["student_id"]),
                                    group_id=str(row["group_id"])) for row in cur.fetchall()]

    if not ranked_students:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=len(scheduled_lecture_ids), students=[]
        )
    # Нет метаданных — пропускаем после LIMIT, не подменяем следующим студентом.
    group_rows = [row for row in ranked_students if row["metadata_group_id"] is not None]

    # Пробуем взять сведения о студентах из кэша.
    top_student_ids = [item["student_id"] for item in ranked_students]
    student_profiles = {}
    try:
        # Читаем всех отобранных студентов одним запросом по UUID.
        cached_profiles = redis_client.mget(top_student_ids)
        for student_id, cached_profile in zip(top_student_ids, cached_profiles):
            if cached_profile:
                try:
                    profile = json.loads(cached_profile)
                    required_fields = {
                        "group_id", "first_name", "second_name", "email", "phone",
                        "student_card_number", "status"
                    }
                    if isinstance(profile, dict) and required_fields.issubset(profile):
                        student_profiles[student_id] = profile
                except (TypeError, json.JSONDecodeError):
                    pass
    except Exception:
        pass

    missing_student_ids = [student_id for student_id in top_student_ids if student_id not in student_profiles]
    if missing_student_ids:
        pg_sql = """
        SELECT id AS student_id, group_id, first_name, second_name,
               email, phone, student_card_number, status
        FROM students
        WHERE id = ANY(%s::uuid[]);
        """
        with closing(get_pg_conn()) as pg_conn:
            with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                # То, чего нет в кэше, читаем из PostgreSQL одним запросом.
                cur.execute(pg_sql, (missing_student_ids,))
                for row in cur.fetchall():
                    student_id = str(row["student_id"])
                    profile = {
                        "student_id": student_id,
                        "group_id": str(row["group_id"]),
                        "first_name": row["first_name"],
                        "second_name": row["second_name"],
                        "email": row["email"],
                        "phone": row["phone"],
                        "student_card_number": row["student_card_number"],
                        "status": row["status"]
                    }
                    student_profiles[student_id] = profile
                    try:
                        # Сохраняем сведения о студенте в кэше на сутки.
                        redis_client.setex(student_id, 86400, json.dumps(profile, ensure_ascii=False))
                    except Exception:
                        pass

    # Получаем названия подразделений из MongoDB.
    department_hierarchy = {}
    target_department_ids = {
        str(row["department_id"]) for row in group_rows
    }
    mongo_uri = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
    try:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=3000) as mongo_client:
            # Находим документы вузов с нужными кафедрами, затем выбираем кафедры из вложенных массивов.
            hierarchy_docs = mongo_client[MONGO_DB]["university_hierarchy"].find(
                {"institutes.departments.department_id": {"$in": list(target_department_ids)}},
                {"_id": 0, "name": 1, "institutes": 1}
            )
            for university in hierarchy_docs:
                for institute in university.get("institutes", []):
                    for department in institute.get("departments", []):
                        department_id = department.get("department_id")
                        if department_id in target_department_ids:
                            department_hierarchy[department_id] = {
                                "department_name": department.get("name"),
                                "institute_name": institute.get("name"),
                                "university_name": university.get("name")
                            }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ошибка чтения иерархии MongoDB"
        )

    if any(dept_id not in department_hierarchy or not all(department_hierarchy[dept_id].values())
           for dept_id in target_department_ids):
        raise HTTPException(status_code=503, detail="Иерархия кафедры в MongoDB неполна")

    # Используем группу из графа: в кэше она могла устареть.
    students_res = []
    groups_by_id = {str(row["group_id"]): row for row in group_rows}
    for stats in ranked_students:
        st_id = stats["student_id"]
        prof = student_profiles.get(st_id)
        group = groups_by_id.get(stats["group_id"])
        if prof and group:
            hierarchy = department_hierarchy.get(str(group["department_id"]), {})
            students_res.append(FullStudentInfoDTO(
                student_id=st_id,
                first_name=prof["first_name"],
                second_name=prof["second_name"],
                email=prof["email"],
                phone=prof["phone"],
                student_card_number=prof["student_card_number"],
                status=prof["status"],
                group_name=group["group_name"],
                speciality_name=group["speciality_name"],
                speciality_code=group["speciality_code"],
                degree_level=group["degree_level"],
                department_name=hierarchy["department_name"],
                institute_name=hierarchy["institute_name"],
                university_name=hierarchy["university_name"],
                total_classes=stats["total_classes"],
                attended_classes=stats["attended_classes"],
                attendance_percentage=stats["attendance_percentage"]
            ))

    report = ReportResponseDTO(
        term=term,
        period_start=period_start,
        period_end=period_end,
        min_attendance_pct=min_attendance_pct,
        max_attendance_pct=max_attendance_pct,
        matching_lectures_count=len(scheduled_lecture_ids),
        students=students_res
    )

    return report
