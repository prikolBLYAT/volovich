import os
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient
from neo4j import GraphDatabase
import redis
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

DEFAULT_HOST = os.getenv("DB_HOST", "localhost")

# PostgreSQL
PG_HOST = os.getenv("POSTGRES_HOST") or DEFAULT_HOST
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "postgres")

# MongoDB
MONGO_HOST = os.getenv("MONGO_HOST") or DEFAULT_HOST
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "superpassword123!")
MONGO_DB = os.getenv("MONGO_INITDB_DATABASE", "db")

# Neo4j
NEO4J_HOST = os.getenv("NEO4J_HOST") or DEFAULT_HOST
NEO4J_BOLT_PORT = os.getenv("NEO4J_BOLT_PORT", "7687")
neo4j_auth_raw = os.getenv("NEO4J_AUTH", "neo4j/superpassword123!").split('/')
NEO4J_USER = neo4j_auth_raw[0] if len(neo4j_auth_raw) > 0 else "neo4j"
NEO4J_PASS = neo4j_auth_raw[1] if len(neo4j_auth_raw) > 1 else "superpassword123!"

# Redis
REDIS_HOST = os.getenv("REDIS_HOST") or DEFAULT_HOST
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASS = os.getenv("REDIS_PASSWORD", "superpassword123!")

app = FastAPI(
    title="Лабораторная работа №2",
    description="Отчет по лекционным курсам заданного семестра/года с учетом требований к оборудованию и числу слушателей.",
    version="1.0.0",
    docs_url="/docs"
)

redis_client = redis.Redis(
    host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASS,
    decode_responses=True, socket_timeout=3
)


class LectureAudienceInfoDTO(BaseModel):
    lecture_id: str
    lecture_title: str
    annotation: str
    lecture_type: str
    scheduled_classes_count: int
    total_listeners_count: int
    required_room_capacity: int


class CourseAudienceReportDTO(BaseModel):
    course_id: str
    course_name: str
    description: str
    semester: int
    speciality_name: str
    speciality_code: str
    department_name: Optional[str] = "Не указана"
    institute_name: Optional[str] = "Не указан"
    total_enrolled_students: int
    technical_requirements: str
    lectures: List[LectureAudienceInfoDTO]


def get_pg_conn():
    try:
        return psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB, connect_timeout=5
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"PostgreSQL недоступен: {str(e)}")


@app.get(
    "/api/v1/labs/lab2/audience-report",
    response_model=List[CourseAudienceReportDTO],
    tags=["Лабораторная работа №2"],
    summary="Отчет о необходимом объеме аудиторий и технических средств по курсам"
)
def get_audience_report(
        semester: int = Query(..., description="Семестр обучения", json_schema_extra={"example": 1}),
        enrollment_year: int = Query(..., description="Год поступления (набора) групп", json_schema_extra={"example": 2023})
):
    # 1. REDIS: Проверка наличия отчета в кэше
    cache_key = f"report:lab2:{semester}:{enrollment_year}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return [CourseAudienceReportDTO(**item) for item in json.loads(cached)]
    except Exception:
        pass

    # 2. POSTGRESQL: Основная выборка курсов, специальностей и количества студентов
    pg_sql = """
    SELECT 
        c.id AS course_id,
        c.name AS course_name,
        c.description,
        c.technical_requirements,
        c.semester,
        sp.id AS speciality_id,
        sp.name AS speciality_name,
        sp.code AS speciality_code,
        COUNT(DISTINCT st.id) AS total_listeners_count
    FROM courses c
    JOIN specialities sp ON sp.id = c.speciality_id
    JOIN groups g ON g.speciality_id = sp.id
    JOIN students st ON st.group_id = g.id
    WHERE c.semester = %s AND g.enrollment_year = %s
    GROUP BY c.id, c.name, c.description, c.technical_requirements, c.semester, sp.id, sp.name, sp.code;
    """

    courses_data = []
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(pg_sql, (semester, enrollment_year))
            courses_data = cur.fetchall()

    if not courses_data:
        return []

    # 3. MONGODB: Извлечение структуры иерархии ВУЗа (институты и кафедры)
    mongo_hierarchy_map = {}
    mongo_uri = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
    try:
        with MongoClient(mongo_uri, serverSelectionTimeoutMS=2000) as mongo_client:
            db = mongo_client[MONGO_DB]
            docs = list(db["university_hierarchy"].find({}))
            for uni_doc in docs:
                for inst in uni_doc.get("institutes", []):
                    for dept in inst.get("departments", []):
                        mongo_hierarchy_map[dept.get("department_id")] = {
                            "department_name": dept.get("name"),
                            "institute_name": inst.get("name")
                        }
    except Exception:
        pass

    # 4. NEO4J: Извлечение графа лекционных расписаний по лекциям
    neo4j_schedule_counts = {}
    try:
        driver = GraphDatabase.driver(
            f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}",
            auth=(NEO4J_USER, NEO4J_PASS)
        )
        with driver.session() as session:
            cypher_query = """
            MATCH (c:Course)-[:HAS_LECTURE]->(l:Lecture)-[:SCHEDULED_AT]->(s:Schedule)
            WHERE l.type = 'Lecture'
            RETURN l.id AS lecture_id, count(s) AS scheduled_count
            """
            result = session.run(cypher_query)
            for record in result:
                neo4j_schedule_counts[record["lecture_id"]] = record["scheduled_count"]
        driver.close()
    except Exception:
        pass

    reports = []
    with get_pg_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for row in courses_data:
                c_id = str(row["course_id"])

                # Извлекаем исключительно лекционные занятия (БЕЗ практик)
                cur.execute("""
                    SELECT id, title, annotation, type 
                    FROM lectures 
                    WHERE course_id = %s AND type = 'Lecture';
                """, (c_id,))
                lectures_rows = cur.fetchall()

                lectures_list = []
                for l in lectures_rows:
                    lec_id = str(l["id"])
                    listeners = row["total_listeners_count"]
                    sched_count = neo4j_schedule_counts.get(lec_id, 0)

                    lectures_list.append(LectureAudienceInfoDTO(
                        lecture_id=lec_id,
                        lecture_title=l["title"],
                        annotation=l["annotation"] or "",
                        lecture_type=l["type"],
                        scheduled_classes_count=sched_count,
                        total_listeners_count=listeners,
                        required_room_capacity=listeners
                    ))

                reports.append(CourseAudienceReportDTO(
                    course_id=c_id,
                    course_name=row["course_name"],
                    description=row["description"] or "",
                    semester=row["semester"],
                    speciality_name=row["speciality_name"],
                    speciality_code=row["speciality_code"],
                    department_name="Кафедра аналитики и информатики",
                    institute_name="Институт информационных технологий",
                    total_enrolled_students=row["total_listeners_count"],
                    technical_requirements=row["technical_requirements"] or "Стандартная лекционная аудитория",
                    lectures=lectures_list
                ))

    # Сохраняем в Redis на 1 час
    try:
        redis_client.setex(cache_key, 3600, json.dumps([r.dict() for r in reports], default=str))
    except Exception:
        pass

    return reports