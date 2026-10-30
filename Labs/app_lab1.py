import os
import json
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
import psycopg2
from psycopg2.extras import RealDictCursor
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase
import redis
from dotenv import load_dotenv

# 1. Загрузка переменных окружения
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

DEFAULT_HOST = os.getenv("DB_HOST", "localhost")

PG_HOST = os.getenv("POSTGRES_HOST") or DEFAULT_HOST
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "postgres")

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


# 2. DTO Схемы с полной информацией о студенте

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


# 3. Вспомогательные функции

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


# 4. Эндпоинты

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
    # ШАГ 0: Проверка кэша в Redis
    if period_start > period_end:
        raise HTTPException(status_code=400, detail="period_start не может быть позже period_end")
    if min_attendance_pct > max_attendance_pct:
        raise HTTPException(status_code=400, detail="min_attendance_pct не может быть больше max_attendance_pct")

    # ШАГ 1: Полнотекстовый поиск в материалах лекций в Elasticsearch
    es = get_es_client()
    try:
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

    # ШАГ 2: В PostgreSQL определяем группы по связям курса и кафедры.
    group_sql = """
    SELECT DISTINCT
        g.id AS group_id,
        g.department_id,
        g.name AS group_name,
        sp.name AS speciality_name,
        sp.code AS speciality_code,
        sp.degree_level,
        d.name AS department_name
    FROM groups g
    JOIN courses c
      ON c.speciality_id = g.speciality_id
     AND c.department_id = g.department_id
    JOIN lectures l ON l.course_id = c.id
    JOIN specialities sp ON sp.id = g.speciality_id
    JOIN departments d ON d.id = g.department_id
    WHERE l.id = ANY(%s::uuid[])
      AND l.type = 'Lecture';
    """
    with closing(get_pg_conn()) as pg_conn:
        with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(group_sql, (lecture_ids,))
            group_rows = cur.fetchall()

    if not group_rows:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=0, students=[]
        )

    # ШАГ 3: Из MongoDB получаем вуз и институт для кафедр подходящих групп.
    department_hierarchy = {}
    target_department_ids = {str(row["department_id"]) for row in group_rows}
    mongo_uri = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
    try:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=3000) as mongo_client:
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
            detail=f"Ошибка чтения иерархии MongoDB: {str(e)}"
        )

    # ШАГ 4: Из Neo4j получаем занятия и студентов соответствующих групп.
    group_ids = [str(row["group_id"]) for row in group_rows]
    student_schedules = {}
    scheduled_lecture_ids = set()
    try:
        with neo4j_driver.session() as session:
            cypher_query = """
            MATCH (st:Student)-[:BELONGS_TO]->(g:Group)
                  -[:HAS_SCHEDULE]->(sc:Schedule)-[:FOR_LECTURE]->(l:Lecture)
                  -[:OF_COURSE]->(c:Course)
            WHERE sc.date >= $p_start AND sc.date <= $p_end
              AND g.id IN $group_ids
              AND l.id IN $lecture_ids
              AND l.type = 'Lecture'
            RETURN st.id AS student_id, g.id AS group_id,
                   collect(DISTINCT sc.id) AS schedule_ids,
                   collect(DISTINCT l.id) AS lecture_ids
            """
            res = session.run(
                cypher_query,
                lecture_ids=lecture_ids,
                group_ids=group_ids,
                p_start=str(period_start),
                p_end=str(period_end)
            )
            for r in res:
                scheduled_lecture_ids.update(r["lecture_ids"])
                student_schedules[r["student_id"]] = {
                    "group_id": r["group_id"],
                    "schedule_ids": r["schedule_ids"]
                }
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

    # ШАГ 5: По полученным расписаниям PostgreSQL считает посещения.
    student_ids = list(student_schedules)
    schedule_ids = list({
        schedule_id
        for student in student_schedules.values()
        for schedule_id in student["schedule_ids"]
    })
    attendance_stats = {}
    if schedule_ids:
        attendance_sql = """
        SELECT student_id, SUM(mark)::int AS attended_classes
        FROM (
            SELECT a.student_id, a.schedule_id, MAX(a.mark) AS mark
            FROM attendance a
            JOIN schedule sc ON sc.id = a.schedule_id
            JOIN students st ON st.id = a.student_id AND st.group_id = sc.group_id
            WHERE a.schedule_id = ANY(%s::uuid[])
              AND a.student_id = ANY(%s::uuid[])
              AND a.date BETWEEN %s AND %s
            GROUP BY a.student_id, a.schedule_id
        ) per_class
        GROUP BY student_id;
        """
        with closing(get_pg_conn()) as pg_conn:
            with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(attendance_sql, (schedule_ids, student_ids, period_start, period_end))
                attendance_stats = {
                    str(row["student_id"]): row["attended_classes"]
                    for row in cur.fetchall()
                }

    ranked_students = []
    for student_id, scoped in student_schedules.items():
        total_classes = len(scoped["schedule_ids"])
        if not total_classes:
            continue
        attended_classes = attendance_stats.get(student_id, 0)
        pct = round(attended_classes / total_classes * 100, 2)
        if min_attendance_pct <= pct <= max_attendance_pct:
            ranked_students.append({
                "student_id": student_id,
                "group_id": scoped["group_id"],
                "total_classes": total_classes,
                "attended_classes": attended_classes,
                "attendance_percentage": pct
            })
    ranked_students.sort(key=lambda item: (
        item["attendance_percentage"], -item["total_classes"], item["student_id"]
    ))
    ranked_students = ranked_students[:limit]

    if not ranked_students:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=len(scheduled_lecture_ids), students=[]
        )

    # ШАГ 6: Профили студентов читаем из Redis по student_id.
    top_student_ids = [item["student_id"] for item in ranked_students]
    student_profiles = {}
    try:
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
                        redis_client.setex(student_id, 86400, json.dumps(profile, ensure_ascii=False))
                    except Exception:
                        pass

    # ШАГ 7: Собираем профиль из Redis, атрибуты группы из PostgreSQL и вуз из MongoDB.
    students_res = []
    groups_by_id = {str(row["group_id"]): row for row in group_rows}
    for stats in ranked_students:
        st_id = stats["student_id"]
        prof = student_profiles.get(st_id)
        group = groups_by_id.get(prof.get("group_id")) if prof else None
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
                department_name=hierarchy.get("department_name") or group["department_name"],
                institute_name=hierarchy.get("institute_name") or "Не указан",
                university_name=hierarchy.get("university_name") or "Не указан",
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
