import os
import uuid
import random
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from faker import Faker

# Импорты драйверов СУБД
import psycopg2
from psycopg2.extras import execute_values
from neo4j import GraphDatabase
import redis
from pymongo import MongoClient
from elasticsearch import Elasticsearch, helpers, ApiError

# Загрузка переменных из .env
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

DB_HOST = os.getenv("DB_HOST", "localhost")

# PostgreSQL
PG_HOST = os.getenv("POSTGRES_HOST", DB_HOST)
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "postgres")

# Neo4j
NEO4J_HOST = os.getenv("NEO4J_HOST", DB_HOST)
NEO4J_BOLT_PORT = os.getenv("NEO4J_BOLT_PORT", "7687")
neo4j_auth_raw = os.getenv("NEO4J_AUTH", "neo4j/superpassword123!").split('/')
NEO4J_USER = neo4j_auth_raw[0] if len(neo4j_auth_raw) > 0 else "neo4j"
NEO4J_PASS = neo4j_auth_raw[1] if len(neo4j_auth_raw) > 1 else "superpassword123!"

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", DB_HOST)
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASS = os.getenv("REDIS_PASSWORD", "superpassword123!")

# MongoDB
MONGO_HOST = os.getenv("MONGO_HOST", DB_HOST)
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "superpassword123!")
MONGO_DB = os.getenv("MONGO_INITDB_DATABASE", "db")

# Elasticsearch
ES_HOST_IP = os.getenv("ES_HOST", DB_HOST)
ES_PORT = os.getenv("ES_PORT", "9200")
ES_PASS = os.getenv("ELASTIC_PASSWORD", "superpassword123!")
ES_USER = "elastic"

fake = Faker('ru_RU')

# Объемы генерации
NUM_UNIVERSITIES = 2
INSTITUTES_PER_UNI = 2
DEPTS_PER_INST = 2
SPECS_PER_DEPT = 2
GROUPS_PER_SPEC = 2
STUDENTS_PER_GROUP = 5
COURSES_PER_SPEC = 2
LECTURES_PER_COURSE = 10
DAYS_OF_SCHEDULE = 5
CLASSES_PER_DAY = 3

UNI_PREFIXES = ["Национальный исследовательский", "Государственный", "Российский", "Московский", "Санкт-Петербургский"]
UNI_TYPES = ["университет", "институт", "академия"]
UNI_SPECS = ["информационных технологий", "кибернетики", "радиотехники", "электроники", "экономики и управления"]

DEPT_TOPICS = [
    "Программирование и информатика", "Кибербезопасность", "Высшая математика",
    "Прикладная физика", "Искусственный интеллект", "Базы данных и аналитика",
    "Сетевые технологии", "Системный анализ", "Веб-технологии"
]

COURSE_NAMES = [
    "Базы данных", "Алгоритмы и структуры данных", "Безопасность операционных систем",
    "Машинное обучение", "Параллельные вычисления", "Разработка на Python",
    "Архитектура ЭВМ", "Компьютерные сети", "Криптография", "Разработка под Web"
]

TECH_EQUIPMENT_OPTIONS = [
    "Проектор, маркерная доска",
    "Проектор, аудиосистема, микрофон преподавателя",
    "Компьютерный класс с GPU-ускорителями, компьютерные рабочие места",
    "Сетевое оборудование, проектор",
    "VR оборудование",
    "Оборудование для физических эксперементов"
]


def generate_random_data():
    print("Генерация случайных данных...")

    universities = []
    institutes = []
    departments = []
    specialities = []
    dept_specs = []
    groups = []
    students = []
    courses = []
    lectures = []
    schedules = []
    attendances = []

    mongo_hierarchy_docs = []
    neo_nodes = []
    neo_relations = []

    base_date = datetime(2026, 9, 1).date()
    spec_counter = 1

    for u_idx in range(NUM_UNIVERSITIES):
        uni_id = str(uuid.uuid4())
        uni_name = f"{random.choice(UNI_PREFIXES)} {random.choice(UNI_TYPES)} {random.choice(UNI_SPECS)}"
        uni_short = "".join([word[0].upper() for word in uni_name.split() if len(word) > 2])[:6]
        uni_address = fake.address()
        uni_website = f"https://www.{uni_short.lower()}.edu.ru"

        universities.append((uni_id, uni_name, uni_short, uni_address, uni_website))

        uni_institutes_doc = []

        for i_idx in range(INSTITUTES_PER_UNI):
            inst_id = str(uuid.uuid4())
            inst_name = f"Институт {random.choice(UNI_SPECS)}"
            inst_short = f"И-{inst_name.split()[-1][:3].upper()}"
            inst_dean = fake.name_male()

            institutes.append((inst_id, uni_id, inst_name, inst_short, inst_dean))

            inst_departments_doc = []

            for d_idx in range(DEPTS_PER_INST):
                dept_id = str(uuid.uuid4())
                dept_name = f"Кафедра '{random.choice(DEPT_TOPICS)}'"
                dept_short = f"КБ-{random.randint(1, 99)}"
                dept_head = fake.name_male()

                departments.append((dept_id, inst_id, dept_name, dept_short, dept_head))

                inst_departments_doc.append({
                    "department_id": dept_id,
                    "name": dept_name,
                    "short_name": dept_short,
                    "head": dept_head
                })

                for s_idx in range(SPECS_PER_DEPT):
                    spec_id = str(uuid.uuid4())
                    code = f"09.03.{spec_counter:04d}"
                    spec_counter += 1
                    spec_name = f"Направление: {random.choice(DEPT_TOPICS)}"

                    specialities.append((spec_id, spec_name, code, "Бакалавриат", 4))
                    dept_specs.append((str(uuid.uuid4()), dept_id, spec_id))

                    group_ids = []
                    for g_idx in range(GROUPS_PER_SPEC):
                        group_id = str(uuid.uuid4())

                        # Год поступления от 2023 до 2026
                        enrollment_year = random.randint(2023, 2026)

                        group_name = f"{dept_short[:2]}-{random.randint(10, 99)}-{enrollment_year % 100}"
                        group_ids.append(group_id)

                        groups.append((group_id, spec_id, dept_id, group_name, enrollment_year, fake.name_male()))

                        for _ in range(STUDENTS_PER_GROUP):
                            st_id = str(uuid.uuid4())
                            is_male = random.choice([True, False])
                            f_name = fake.first_name_male() if is_male else fake.first_name_female()
                            l_name = fake.last_name_male() if is_male else fake.last_name_female()
                            card_num = str(fake.unique.random_number(digits=8, fix_len=True))

                            students.append((
                                st_id, group_id, f_name, l_name,
                                fake.unique.email(), fake.phone_number(), card_num, "active"
                            ))

                            neo_nodes.append(("Student", {
                                "id": st_id, "first_name": f_name, "second_name": l_name, "card": card_num
                            }))

                    for c_idx in range(COURSES_PER_SPEC):
                        course_id = str(uuid.uuid4())
                        c_name = random.choice(COURSE_NAMES)
                        total_hours = random.choice([72, 108, 144])
                        lec_hours = total_hours // 2
                        prac_hours = total_hours // 2

                        tech_reqs = f"Технические средства: {random.choice(TECH_EQUIPMENT_OPTIONS)} ({fake.catch_phrase()})"

                        courses.append((
                            course_id, spec_id, c_name, fake.paragraph(nb_sentences=1),
                            tech_reqs, random.randint(1, 8), total_hours, lec_hours, prac_hours
                        ))

                        neo_nodes.append(("Course", {"id": course_id, "name": c_name}))

                        course_lectures = []
                        for cl_idx in range(LECTURES_PER_COURSE):
                            lec_id = str(uuid.uuid4())
                            lec_item = {
                                "id": lec_id,
                                "course_id": course_id,
                                "title": f"{c_name}: {fake.sentence(nb_words=3)}",
                                "annotation": fake.paragraph(nb_sentences=2),
                                "content": fake.text(max_nb_chars=500),
                                "type": random.choice(["Lecture", "Practice", "Lab"])
                            }
                            lectures.append(lec_item)
                            course_lectures.append(lec_item)

                            neo_nodes.append(
                                ("Lecture", {"id": lec_id, "title": lec_item["title"], "type": lec_item["type"]}))
                            neo_relations.append((course_id, lec_id, "HAS_LECTURE"))

                        if course_lectures and group_ids:
                            for day_offset in range(DAYS_OF_SCHEDULE):
                                sch_date = base_date + timedelta(days=day_offset)

                                for p in range(1, CLASSES_PER_DAY + 1):
                                    selected_lec = random.choice(course_lectures)
                                    selected_grp = random.choice(group_ids)
                                    sch_id = str(uuid.uuid4())
                                    teacher_name = fake.name_male()

                                    schedules.append({
                                        "id": sch_id,
                                        "class_id": selected_lec["id"],
                                        "group_id": selected_grp,
                                        "date": sch_date,
                                        "period": p,
                                        "room": f"Аудитория {random.randint(100, 599)}",
                                        "teacher": teacher_name
                                    })

                                    neo_nodes.append(("Schedule", {
                                        "id": sch_id, "date": str(sch_date), "period": p,
                                        "room": f"Аудитория {random.randint(100, 599)}"
                                    }))
                                    neo_relations.append((selected_lec["id"], sch_id, "SCHEDULED_AT"))

                                    grp_students = [s for s in students if s[1] == selected_grp]
                                    now_ts = datetime.now()
                                    for st in grp_students:
                                        att_id = str(uuid.uuid4())
                                        mark_val = random.choice([0, 1])
                                        attendances.append((
                                            att_id, sch_id, st[0], sch_date, p, mark_val, now_ts
                                        ))

                                        neo_nodes.append(
                                            ("Attendance", {"id": att_id, "mark": mark_val, "date": str(sch_date)}))
                                        neo_relations.append((st[0], att_id, "HAS_ATTENDANCE"))
                                        neo_relations.append((att_id, sch_id, "FOR_SCHEDULE"))

            uni_institutes_doc.append({
                "institute_id": inst_id,
                "name": inst_name,
                "short_name": inst_short,
                "dean": inst_dean,
                "departments": inst_departments_doc
            })

        mongo_hierarchy_docs.append({
            "university_id": uni_id,
            "name": uni_name,
            "short_name": uni_short,
            "address": uni_address,
            "website": uni_website,
            "institutes": uni_institutes_doc
        })

    return {
        "universities": universities,
        "institutes": institutes,
        "departments": departments,
        "specialities": specialities,
        "dept_specs": dept_specs,
        "groups": groups,
        "students": students,
        "courses": courses,
        "lectures": lectures,
        "schedules": schedules,
        "attendances": attendances,
        "mongo_hierarchy": mongo_hierarchy_docs,
        "neo_nodes": neo_nodes,
        "neo_relations": neo_relations
    }


def init_pg_tables(pg_cur):
    pg_cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    pg_cur.execute("""
    CREATE TABLE IF NOT EXISTS universities (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(255) NOT NULL, short_name VARCHAR(50), address TEXT, website VARCHAR(255)
    );
    CREATE TABLE IF NOT EXISTS institutes (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        university_id UUID NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL, short_name VARCHAR(50), dean VARCHAR(150)
    );
    CREATE TABLE IF NOT EXISTS departments (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        institute_id UUID NOT NULL REFERENCES institutes(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL, short_name VARCHAR(50), head VARCHAR(150)
    );
    CREATE TABLE IF NOT EXISTS specialities (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(255) NOT NULL, code VARCHAR(50) NOT NULL UNIQUE, degree_level VARCHAR(50), duration INT
    );
    CREATE TABLE IF NOT EXISTS department_specialties (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        department_id UUID NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        specialty_id UUID NOT NULL REFERENCES specialities(id) ON DELETE CASCADE,
        CONSTRAINT idx_dept_spec UNIQUE (department_id, specialty_id)
    );
    CREATE TABLE IF NOT EXISTS groups (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        speciality_id UUID NOT NULL REFERENCES specialities(id) ON DELETE CASCADE,
        department_id UUID REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(100) NOT NULL, enrollment_year INT NOT NULL, curator VARCHAR(150)
    );
    CREATE TABLE IF NOT EXISTS students (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        first_name VARCHAR(100) NOT NULL, second_name VARCHAR(100) NOT NULL,
        email VARCHAR(150) UNIQUE, phone VARCHAR(30), student_card_number VARCHAR(50) UNIQUE NOT NULL,
        status VARCHAR(30) DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS courses (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        speciality_id UUID NOT NULL REFERENCES specialities(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL, description TEXT, technical_requirements TEXT, semester INT NOT NULL,
        duration_hours INT, lecture_hours INT, practics_hours INT
    );
    CREATE TABLE IF NOT EXISTS lectures (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
        title VARCHAR(255) NOT NULL, annotation TEXT, type VARCHAR(50)
    );
    CREATE TABLE IF NOT EXISTS schedule (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        class_id UUID NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
        group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        schedule_date DATE NOT NULL, period INT NOT NULL, room VARCHAR(50), teacher VARCHAR(150)
    );

    -- Партиции ATTENDANCE
    CREATE TABLE IF NOT EXISTS attendance (
        id UUID NOT NULL DEFAULT uuid_generate_v4(),
        schedule_id UUID NOT NULL REFERENCES schedule(id) ON DELETE CASCADE,
        student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        date DATE NOT NULL,
        period INT NOT NULL,
        mark INT NOT NULL,
        marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id, date)
    ) PARTITION BY RANGE (date);

    CREATE INDEX IF NOT EXISTS idx_students_group ON students(group_id);
    CREATE INDEX IF NOT EXISTS idx_schedule_group_date ON schedule(group_id, schedule_date);
    CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
    """)


def create_attendance_partitions(pg_cur, start_year=2026, start_month=9, months_count=4):
    for i in range(months_count):
        m = (start_month - 1 + i) % 12 + 1
        y = start_year + (start_month - 1 + i) // 12

        next_m = m % 12 + 1
        next_y = y if m < 12 else y + 1

        partition_name = f"attendance_y{y}m{m:02d}"
        from_date = f"{y}-{m:02d}-01"
        to_date = f"{next_y}-{next_m:02d}-01"

        pg_cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF attendance
            FOR VALUES FROM ('{from_date}') TO ('{to_date}');
        """)
    print("Партиции таблицы attendance успешно созданы!")


def init_es_index(es_client):
    index_name = "lectures"
    try:
        exists = es_client.indices.exists(index=index_name)
    except ApiError:
        exists = False

    if not exists:
        settings = {
            "analysis": {
                "analyzer": {
                    "russian_analyzer": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "russian_stop", "russian_stemmer"]
                    }
                },
                "filter": {
                    "russian_stop": {"type": "stop", "stopwords": "_russian_"},
                    "russian_stemmer": {"type": "stemmer", "language": "russian"}
                }
            }
        }
        mappings = {
            "properties": {
                "lecture_id": {"type": "keyword"},
                "course_id": {"type": "keyword"},
                "title": {"type": "text", "analyzer": "russian_analyzer", "fields": {"keyword": {"type": "keyword"}}},
                "annotation": {"type": "text", "analyzer": "russian_analyzer"},
                "content_text": {"type": "text", "analyzer": "russian_analyzer"},
                "type": {"type": "keyword"},
                "created_at": {"type": "date"}
            }
        }
        es_client.indices.create(index=index_name, settings=settings, mappings=mappings)


def run_seeder():
    data = generate_random_data()

    print(f"Сгенерировано:")
    print(f" - universities: {len(data['universities'])}")
    print(f" - students: {len(data['students'])}")
    print(f" - lectures: {len(data['lectures'])}")
    print(f" - schedules: {len(data['schedules'])}")
    print(f" - attendances: {len(data['attendances'])}")

    # 1. PostgreSQL
    print("\nЗаполнение PostgreSQL")
    with psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB) as pg_conn:
        with pg_conn.cursor() as pg_cur:
            pg_cur.execute("""
                DROP TABLE IF EXISTS attendance, schedule, lectures, classes, courses, 
                students, groups, department_specialties, specialities, departments, 
                institutes, universities CASCADE;
            """)
            init_pg_tables(pg_cur)
            create_attendance_partitions(pg_cur, start_year=2026, start_month=9, months_count=4)

            execute_values(pg_cur,
                           "INSERT INTO universities (id, name, short_name, address, website) VALUES %s ON CONFLICT DO NOTHING",
                           data["universities"])
            execute_values(pg_cur,
                           "INSERT INTO institutes (id, university_id, name, short_name, dean) VALUES %s ON CONFLICT DO NOTHING",
                           data["institutes"])
            execute_values(pg_cur,
                           "INSERT INTO departments (id, institute_id, name, short_name, head) VALUES %s ON CONFLICT DO NOTHING",
                           data["departments"])
            execute_values(pg_cur,
                           "INSERT INTO specialities (id, name, code, degree_level, duration) VALUES %s ON CONFLICT (code) DO NOTHING",
                           data["specialities"])
            execute_values(pg_cur,
                           "INSERT INTO department_specialties (id, department_id, specialty_id) VALUES %s ON CONFLICT DO NOTHING",
                           data["dept_specs"])
            execute_values(pg_cur,
                           "INSERT INTO groups (id, speciality_id, department_id, name, enrollment_year, curator) VALUES %s ON CONFLICT DO NOTHING",
                           data["groups"])
            execute_values(pg_cur,
                           "INSERT INTO students (id, group_id, first_name, second_name, email, phone, student_card_number, status) VALUES %s ON CONFLICT DO NOTHING",
                           data["students"])
            execute_values(pg_cur,
                           "INSERT INTO courses (id, speciality_id, name, description, technical_requirements, semester, duration_hours, lecture_hours, practics_hours) VALUES %s ON CONFLICT DO NOTHING",
                           data["courses"])

            lectures_tuples = [(l["id"], l["course_id"], l["title"], l["annotation"], l["type"]) for l in
                               data["lectures"]]
            execute_values(pg_cur,
                           "INSERT INTO lectures (id, course_id, title, annotation, type) VALUES %s ON CONFLICT DO NOTHING",
                           lectures_tuples)

            sched_tuples = [(s["id"], s["class_id"], s["group_id"], s["date"], s["period"], s["room"], s["teacher"]) for
                            s in data["schedules"]]
            execute_values(pg_cur,
                           "INSERT INTO schedule (id, class_id, group_id, schedule_date, period, room, teacher) VALUES %s ON CONFLICT DO NOTHING",
                           sched_tuples)

            execute_values(pg_cur,
                           "INSERT INTO attendance (id, schedule_id, student_id, date, period, mark, marked_at) VALUES %s ON CONFLICT DO NOTHING",
                           data["attendances"])
    print("PostgreSQL готова!")

    # 2. Neo4j
    print("\nЗаполнение Neo4j")
    with GraphDatabase.driver(f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}", auth=(NEO4J_USER, NEO4J_PASS)) as neo4j_driver:
        with neo4j_driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n;")

            nodes_by_label = {}
            for label, props in data["neo_nodes"]:
                nodes_by_label.setdefault(label, {})[props["id"]] = props

            for label, props_dict in nodes_by_label.items():
                session.run(
                    f"UNWIND $batch AS row MERGE (n:{label} {{id: row.id}}) SET n += row",
                    batch=list(props_dict.values())
                )

            rels_by_type = {}
            for src_id, dst_id, rel_type in data["neo_relations"]:
                rels_by_type.setdefault(rel_type, []).append({"src": src_id, "dst": dst_id})

            for rel_type, rel_list in rels_by_type.items():
                session.run(
                    f"UNWIND $batch AS row MATCH (a {{id: row.src}}), (b {{id: row.dst}}) MERGE (a)-[:{rel_type}]->(b)",
                    batch=rel_list
                )
    print("Neo4j готова!")

    # 3. Redis (Очистка кэша)
    print("\nЗаполнение Redis")
    r_client = redis.Redis(host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASS, decode_responses=True)
    r_client.flushall()
    print("Кэш Redis очищен!")
    r_client.close()

    # 4. MongoDB (Исключительно коллекция university_hierarchy)
    print("\nЗаполнение MongoDB")
    mongo_uri = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
    with MongoClient(mongo_uri) as mongo_client:
        mongo_db = mongo_client[MONGO_DB]
        mongo_db.drop_collection("course_materials")
        mongo_collection = mongo_db["university_hierarchy"]
        mongo_collection.delete_many({})

        if data["mongo_hierarchy"]:
            res = mongo_collection.insert_many(data["mongo_hierarchy"])
    print("MongoDB готова!")

    # 5. Elasticsearch
    print("\nЗаполнение Elasticsearch")
    with Elasticsearch([f"http://{ES_HOST_IP}:{ES_PORT}"], basic_auth=(ES_USER, ES_PASS)) as es_client:
        init_es_index(es_client)

        es_actions = []
        for lec in data["lectures"]:
            es_actions.append({
                "_index": "lectures",
                "_id": lec["id"],
                "_source": {
                    "lecture_id": lec["id"],
                    "course_id": lec["course_id"],
                    "title": lec["title"],
                    "annotation": lec["annotation"],
                    "content_text": lec["content"],
                    "type": lec["type"],
                    "created_at": datetime.now().isoformat()
                }
            })
        if es_actions:
            helpers.bulk(es_client, es_actions)
    print("Elasticsearch готова!")


if __name__ == "__main__":
    run_seeder()