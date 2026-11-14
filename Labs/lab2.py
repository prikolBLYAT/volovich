import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel
from pymongo import MongoClient
from neo4j import GraphDatabase
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path if os.path.exists(env_path) else None)

DEFAULT_HOST = os.getenv("DB_HOST", "localhost")

# Подключение к MongoDB.
MONGO_HOST = os.getenv("MONGO_HOST") or DEFAULT_HOST
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "superpassword123!")
MONGO_DB = os.getenv("MONGO_INITDB_DATABASE", "db")

# Подключение к Neo4j.
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
    # В графе отбираем курсы, лекции и группы, затем считаем занятия и слушателей.
    try:
        with GraphDatabase.driver(
            f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}",
            auth=(NEO4J_USER, NEO4J_PASS)
        ) as driver:
            with driver.session() as session:
                cypher_query = """
                MATCH (g:Group)
                WHERE g.enrollment_year = $enrollment_year
                MATCH (g)-[:HAS_SCHEDULE]->(sc:Schedule)-[:FOR_LECTURE]->(l:Lecture)
                      -[:OF_COURSE]->(c:Course)
                WHERE c.semester = $semester
                  AND l.type = 'Lecture'
                  AND l.technical_requirements = true
                WITH c, l, g, count(DISTINCT sc) AS scheduled_count
                MATCH (st:Student)-[:BELONGS_TO]->(g)
                RETURN c.id AS course_id, c.name AS course_name,
                       c.description AS description, c.semester AS semester,
                       c.department_id AS department_id,
                       c.speciality_id AS speciality_id,
                       c.speciality_name AS speciality_name,
                       c.speciality_code AS speciality_code,
                       c.duration_hours AS duration_hours,
                       c.lecture_hours AS lecture_hours,
                       c.practice_hours AS practice_hours,
                       l.id AS lecture_id, l.title AS lecture_title,
                       l.annotation AS annotation, l.type AS lecture_type,
                       l.technical_requirements AS technical_requirements,
                       g.id AS group_id, scheduled_count,
                       count(DISTINCT st) AS listeners_count
                """
                # Студентов считаем после занятий, чтобы не посчитать одного человека несколько раз.
                result = session.run(
                    cypher_query, semester=semester, enrollment_year=enrollment_year
                )
                graph_rows = [dict(record) for record in result
                              if record["listeners_count"] > 0 and record["scheduled_count"] > 0]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Neo4j недоступен: {str(e)}")

    if not graph_rows:
        return []
    courses = {}
    lecture_stats = {}
    course_groups = {}
    required_fields = (
        "course_id", "course_name", "semester", "department_id",
        "speciality_id", "speciality_name", "speciality_code",
        "lecture_id", "lecture_title", "lecture_type", "technical_requirements", "group_id"
    )
    for row in graph_rows:
        if any(row.get(field) is None or row[field] == "" for field in required_fields):
            raise HTTPException(status_code=503, detail="Обязательные метаданные Neo4j неполны")
        course_id = row["course_id"]
        lecture_id = row["lecture_id"]
        course = courses.setdefault(course_id, {"info": row, "lectures": {}})
        course["lectures"][lecture_id] = row
        stats = lecture_stats.setdefault(lecture_id, {"scheduled_count": 0, "listeners_count": 0})
        stats["scheduled_count"] += row["scheduled_count"]
        stats["listeners_count"] += row["listeners_count"]
        # В числе слушателей курса каждую группу учитываем один раз.
        course_groups.setdefault(course_id, {})[row["group_id"]] = row["listeners_count"]

    # За названиями подразделений идём после отбора курсов.
    department_ids = sorted({str(course["info"]["department_id"]) for course in courses.values()})
    department_map = {}
    try:
        with MongoClient(
            host=MONGO_HOST, port=int(MONGO_PORT), username=MONGO_USER,
            password=MONGO_PASS, authSource="admin", serverSelectionTimeoutMS=2000
        ) as mongo_client:
            # Находим в MongoDB кафедры отобранных курсов, их институты и вузы.
            documents = mongo_client[MONGO_DB]["university_hierarchy"].find(
                {"institutes.departments.department_id": {"$in": department_ids}},
                {"_id": 0, "university_id": 1, "name": 1, "institutes": 1}
            )
            for university in documents:
                for institute in university.get("institutes", []):
                    for department in institute.get("departments", []):
                        if department.get("department_id") in department_ids:
                            department_map[department["department_id"]] = {
                                "department_name": department.get("name"),
                                "institute_name": institute.get("name"),
                                "university_id": university.get("university_id"),
                                "university_name": university.get("name")
                            }
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB недоступна")
    if any(dept_id not in department_map or not all(department_map[dept_id].values())
           for dept_id in department_ids):
        raise HTTPException(status_code=503, detail="Иерархия кафедры в MongoDB неполна")

    reports = []
    for course_id, course in sorted(courses.items()):
        row = course["info"]
        department_id = str(row["department_id"])
        department = department_map.get(department_id, {})
        university_id = department["university_id"]
        lectures = []
        for lecture_id, lecture in sorted(course["lectures"].items()):
            stats = lecture_stats[lecture_id]
            lectures.append(LectureAudienceInfoDTO(
                lecture_id=lecture_id,
                lecture_title=lecture["lecture_title"],
                annotation=lecture["annotation"] or "",
                lecture_type=lecture["lecture_type"],
                technical_requirements=lecture["technical_requirements"],
                scheduled_classes_count=stats["scheduled_count"],
                required_room_capacity=stats["listeners_count"]
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
            university_name=department["university_name"],
            duration_hours=row["duration_hours"],
            lecture_hours=row["lecture_hours"],
            practice_hours=row["practice_hours"],
            total_enrolled_students=sum(course_groups.get(course_id, {}).values()),
            lectures=lectures
        ))

    return reports
