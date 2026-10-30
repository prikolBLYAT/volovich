import os
import json
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
        min_attendance_pct: float = Query(0.0, description="Минимальный процент посещаемости (%)",
                                          json_schema_extra={"example": 0.0}),
        max_attendance_pct: float = Query(100.0, description="Максимальный процент посещаемости (%)",
                                          json_schema_extra={"example": 100.0}),
        limit: int = Query(10, description="Лимит топа студентов", json_schema_extra={"example": 10})
):
    # ШАГ 0: Проверка кэша в Redis
    cache_key = f"report:lab1:{term}:{period_start}:{period_end}:{min_attendance_pct}:{max_attendance_pct}:{limit}"
    try:
        cached_report = redis_client.get(cache_key)
        if cached_report:
            return json.loads(cached_report)
    except Exception:
        pass

    # ШАГ 1: Полнотекстовый поиск термина в Elasticsearch
    es = get_es_client()
    try:
        es_res = es.search(
            index="lectures",
            body={
                "query": {
                    "multi_match": {
                        "query": term,
                        "fields": ["title^3", "annotation^2", "content_text"]
                    }
                },
                "_source": ["lecture_id"]
            },
            size=2000
        )
        hits = es_res["hits"]["hits"]
        lecture_ids = [hit["_source"]["lecture_id"] for hit in hits]
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

    # ШАГ 2: Вычисление посещаемости в графе Neo4j
    graph_stats = {}
    try:
        with neo4j_driver.session() as session:
            cypher_query = """
            UNWIND $lecture_ids AS lec_id
            MATCH (l:Lecture {id: lec_id})-[:SCHEDULED_AT]->(sc:Schedule)
            WHERE sc.date >= $p_start AND sc.date <= $p_end
            MATCH (att:Attendance)-[:FOR_SCHEDULE]->(sc)
            MATCH (st:Student)-[:HAS_ATTENDANCE]->(att)
            WITH st, count(att) AS total_classes, sum(att.mark) AS attended_classes
            WITH st, total_classes, attended_classes, 
                 round((toFloat(attended_classes) / total_classes) * 100, 2) AS pct
            WHERE pct >= $min_pct AND pct <= $max_pct
            ORDER BY pct ASC, total_classes DESC
            LIMIT $limit
            RETURN st.id AS student_id, total_classes, attended_classes, pct AS attendance_percentage
            """
            res = session.run(
                cypher_query,
                lecture_ids=lecture_ids,
                p_start=str(period_start),
                p_end=str(period_end),
                min_pct=min_attendance_pct,
                max_pct=max_attendance_pct,
                limit=limit
            )
            for r in res:
                graph_stats[r["student_id"]] = {
                    "total_classes": r["total_classes"],
                    "attended_classes": r["attended_classes"],
                    "attendance_percentage": r["attendance_percentage"]
                }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ошибка графового расчета в Neo4j: {str(e)}"
        )

    if not graph_stats:
        return ReportResponseDTO(
            term=term, period_start=period_start, period_end=period_end,
            min_attendance_pct=min_attendance_pct, max_attendance_pct=max_attendance_pct,
            matching_lectures_count=len(lecture_ids), students=[]
        )

    # ШАГ 3: Запрос полной информации о найденных студентах из PostgreSQL по их UUID
    top_student_ids = list(graph_stats.keys())
    pg_sql = """
    SELECT 
        st.id AS student_id, 
        st.first_name, 
        st.second_name, 
        st.email, 
        st.phone, 
        st.student_card_number, 
        st.status,
        g.name AS group_name,
        sp.name AS speciality_name,
        sp.code AS speciality_code,
        sp.degree_level,
        d.name AS department_name,
        i.name AS institute_name,
        u.name AS university_name
    FROM students st
    JOIN groups g ON g.id = st.group_id
    JOIN specialities sp ON sp.id = g.speciality_id
    JOIN departments d ON d.id = g.department_id
    JOIN institutes i ON i.id = d.institute_id
    JOIN universities u ON u.id = i.university_id
    WHERE st.id = ANY(%s::uuid[]);
    """

    student_profiles = {}
    with get_pg_conn() as pg_conn:
        with pg_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(pg_sql, (top_student_ids,))
            for row in cur.fetchall():
                student_profiles[str(row["student_id"])] = row

    # ШАГ 4: Сборка ответа с полной инфой по студентам
    students_res = []
    for st_id in top_student_ids:
        prof = student_profiles.get(st_id)
        stats = graph_stats[st_id]
        if prof:
            students_res.append(FullStudentInfoDTO(
                student_id=st_id,
                first_name=prof["first_name"],
                second_name=prof["second_name"],
                email=prof["email"],
                phone=prof["phone"],
                student_card_number=prof["student_card_number"],
                status=prof["status"],
                group_name=prof["group_name"],
                speciality_name=prof["speciality_name"],
                speciality_code=prof["speciality_code"],
                degree_level=prof["degree_level"],
                department_name=prof["department_name"],
                institute_name=prof["institute_name"],
                university_name=prof["university_name"],
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
        matching_lectures_count=len(lecture_ids),
        students=students_res
    )

    # ШАГ 5: Кэширование в Redis
    try:
        payload = report.dict()
        payload["period_start"] = str(payload["period_start"])
        payload["period_end"] = str(payload["period_end"])
        redis_client.setex(cache_key, 3600, json.dumps(payload))
    except Exception:
        pass

    return report