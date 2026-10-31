import os
from contextlib import closing
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from pymongo import MongoClient
from neo4j import GraphDatabase
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

app = FastAPI(
    title="Лабораторная работа №2",
    description="Отчет по лекционным курсам заданного семестра/года с учетом требований к оборудованию и числу слушателей.",
    version="1.0.0",
    docs_url="/docs"
)

class LectureAudienceInfoDTO(BaseModel):
    lecture_id: str
    lecture_title: str
    annotation: str
    lecture_type: str
    technical_requirements: bool
    scheduled_classes_count: int
    total_listeners_count: int
    required_room_capacity: int


class CourseAudienceReportDTO(BaseModel):
    course_id: str
    course_name: str
    description: str
    semester: int
    enrollment_year: int
    speciality_id: str
    speciality_name: str
    speciality_code: str
    department_id: str
    department_name: Optional[str] = None
    institute_name: Optional[str] = None
    university_id: Optional[str] = None
    university_name: Optional[str] = None
    duration_hours: Optional[int] = None
    lecture_hours: Optional[int] = None
    practice_hours: Optional[int] = None
    total_enrolled_students: int
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
        semester: int = Query(..., ge=1, description="Семестр обучения", json_schema_extra={"example": 1}),
        enrollment_year: int = Query(..., ge=1, description="Год поступления (набора) групп", json_schema_extra={"example": 2023})
):
    # 1. PostgreSQL: одним запросом отбираем курсы и лекции с техтребованиями.
    pg_sql = """
    SELECT
        c.id AS course_id,
        c.name AS course_name,
        c.description,
        c.semester,
        c.department_id,
        c.duration_hours,
        c.lecture_hours,
        c.practice_hours,
        sp.id AS speciality_id,
        sp.name AS speciality_name,
        sp.code AS speciality_code,
        l.id AS lecture_id,
        l.title AS lecture_title,
        l.annotation,
        l.type AS lecture_type,
        l.technical_requirements
    FROM courses c
    JOIN specialities sp ON sp.id = c.speciality_id
    JOIN lectures l ON l.course_id = c.id
    WHERE c.semester = %s
      AND l.type = 'Lecture'
      AND l.technical_requirements IS TRUE
    ORDER BY c.id, l.id;
    """

    with closing(get_pg_conn()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(pg_sql, (semester,))
            lecture_rows = cur.fetchall()

    if not lecture_rows:
        return []

    courses = {}
    for row in lecture_rows:
        course_id = str(row["course_id"])
        courses.setdefault(course_id, {"info": row, "lectures": []})["lectures"].append(row)
    lecture_ids = [str(row["lecture_id"]) for row in lecture_rows]

    # 2. Neo4j: группы выбранного набора и их студенты, без дублирования по занятиям.
    lecture_stats = {}
    course_groups = {}
    try:
        with GraphDatabase.driver(
            f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}",
            auth=(NEO4J_USER, NEO4J_PASS)
        ) as driver:
            with driver.session() as session:
                cypher_query = """
                MATCH (c:Course)<-[:OF_COURSE]-(l:Lecture)
                      <-[:FOR_LECTURE]-(sc:Schedule)<-[:HAS_SCHEDULE]-(g:Group)
                WHERE c.id IN $course_ids AND l.id IN $lecture_ids
                  AND g.enrollment_year = $enrollment_year
                WITH c, l, g, count(DISTINCT sc) AS scheduled_count
                OPTIONAL MATCH (st:Student)-[:BELONGS_TO]->(g)
                RETURN c.id AS course_id, l.id AS lecture_id, g.id AS group_id,
                       scheduled_count, count(DISTINCT st) AS listeners_count
                """
                result = session.run(
                    cypher_query, course_ids=list(courses), lecture_ids=lecture_ids,
                    enrollment_year=enrollment_year
                )
                for record in result:
                    course_id = record["course_id"]
                    lecture_id = record["lecture_id"]
                    group_id = record["group_id"]
                    listeners = record["listeners_count"]
                    stats = lecture_stats.setdefault(lecture_id, {
                        "scheduled_count": 0, "listeners_count": 0, "room_capacity": 0
                    })
                    stats["scheduled_count"] += record["scheduled_count"]
                    stats["listeners_count"] += listeners
                    stats["room_capacity"] = max(stats["room_capacity"], listeners)
                    # Студент принадлежит одной группе; повторные лекции не увеличивают итог курса.
                    course_groups.setdefault(course_id, {})[group_id] = listeners
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Neo4j недоступен: {str(e)}")

    courses = {course_id: course for course_id, course in courses.items() if course_id in course_groups}
    if not courses:
        return []

    # 3. PostgreSQL: кафедра -> институт -> идентификатор университета, только для найденных курсов.
    department_ids = sorted({str(course["info"]["department_id"]) for course in courses.values()})
    department_map = {}
    with closing(get_pg_conn()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT d.id AS department_id, d.name AS department_name,
                       i.name AS institute_name, i.university_id
                FROM departments d
                JOIN institutes i ON i.id = d.institute_id
                WHERE d.id = ANY(%s::uuid[]);
            """, (department_ids,))
            department_map = {str(row["department_id"]): row for row in cur.fetchall()}

    # 4. MongoDB: только документы нужных университетов, без загрузки всей иерархии.
    university_ids = sorted({str(row["university_id"]) for row in department_map.values()})
    university_map = {}
    if university_ids:
        try:
            with MongoClient(
                host=MONGO_HOST, port=int(MONGO_PORT), username=MONGO_USER,
                password=MONGO_PASS, authSource="admin", serverSelectionTimeoutMS=2000
            ) as mongo_client:
                documents = mongo_client[MONGO_DB]["university_hierarchy"].find(
                    {"university_id": {"$in": university_ids}},
                    {"_id": 0, "university_id": 1, "name": 1}
                )
                university_map = {doc["university_id"]: doc["name"] for doc in documents}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"MongoDB недоступна: {str(e)}")

    reports = []
    for course_id, course in courses.items():
        row = course["info"]
        department_id = str(row["department_id"])
        department = department_map.get(department_id, {})
        university_id = str(department["university_id"]) if department else None
        lectures = []
        for lecture in course["lectures"]:
            lecture_id = str(lecture["lecture_id"])
            stats = lecture_stats.get(lecture_id, {})
            lectures.append(LectureAudienceInfoDTO(
                lecture_id=lecture_id,
                lecture_title=lecture["lecture_title"],
                annotation=lecture["annotation"] or "",
                lecture_type=lecture["lecture_type"],
                technical_requirements=lecture["technical_requirements"],
                scheduled_classes_count=stats.get("scheduled_count", 0),
                total_listeners_count=stats.get("listeners_count", 0),
                required_room_capacity=stats.get("room_capacity", 0)
            ))
        reports.append(CourseAudienceReportDTO(
            course_id=course_id,
            course_name=row["course_name"],
            description=row["description"] or "",
            semester=row["semester"],
            enrollment_year=enrollment_year,
            speciality_id=str(row["speciality_id"]),
            speciality_name=row["speciality_name"],
            speciality_code=row["speciality_code"],
            department_id=department_id,
            department_name=department.get("department_name"),
            institute_name=department.get("institute_name"),
            university_id=university_id,
            university_name=university_map.get(university_id),
            duration_hours=row["duration_hours"],
            lecture_hours=row["lecture_hours"],
            practice_hours=row["practice_hours"],
            total_enrolled_students=sum(course_groups.get(course_id, {}).values()),
            lectures=lectures
        ))

    return reports
