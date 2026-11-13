import os
from contextlib import ExitStack, closing
from dotenv import load_dotenv

# Драйверы баз данных.
import psycopg2
from neo4j import GraphDatabase
import redis
from pymongo import MongoClient
from elasticsearch import Elasticsearch

# Настройки те же, что у генератора.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

DB_HOST = os.getenv("DB_HOST", "localhost")

# Подключение к PostgreSQL.
PG_HOST = os.getenv("POSTGRES_HOST", DB_HOST)
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "postgres")

# Подключение к Neo4j.
NEO4J_HOST = os.getenv("NEO4J_HOST", DB_HOST)
NEO4J_BOLT_PORT = os.getenv("NEO4J_BOLT_PORT", "7687")
neo4j_auth_raw = os.getenv("NEO4J_AUTH", "neo4j/superpassword123!").split('/')
NEO4J_USER = neo4j_auth_raw[0] if len(neo4j_auth_raw) > 0 else "neo4j"
NEO4J_PASS = neo4j_auth_raw[1] if len(neo4j_auth_raw) > 1 else "superpassword123!"

# Подключение к Redis.
REDIS_HOST = os.getenv("REDIS_HOST", DB_HOST)
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_PASS = os.getenv("REDIS_PASSWORD", "superpassword123!")

# Подключение к MongoDB.
MONGO_HOST = os.getenv("MONGO_HOST", DB_HOST)
MONGO_PORT = os.getenv("MONGO_PORT", "27017")
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "superpassword123!")
MONGO_DB = os.getenv("MONGO_INITDB_DATABASE", "db")

# Подключение к Elasticsearch.
ES_HOST_IP = os.getenv("ES_HOST", DB_HOST)
ES_PORT = os.getenv("ES_PORT", "9200")
ES_PASS = os.getenv("ELASTIC_PASSWORD", "superpassword123!")
ES_USER = "elastic"


def run_undo():
    """Удаляем данные генератора. Общей транзакции на все базы нет."""
    phase = 'подключение PostgreSQL'
    try:
        with ExitStack() as stack:
            pg_conn = stack.enter_context(closing(psycopg2.connect(
                host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS,
                dbname=PG_DB, connect_timeout=5,
                options='-c search_path=public -c lock_timeout=5000 -c statement_timeout=30000',
            )))
            stack.enter_context(pg_conn)
            cur = stack.enter_context(pg_conn.cursor())
            # Проверяем, есть ли таблица студентов после прошлого запуска.
            cur.execute("SELECT to_regclass('public.students') IS NOT NULL")
            student_ids = []
            students_table = cur.fetchone()
            if students_table and students_table[0]:
                # Сохраняем UUID студентов: по ним будем удалять записи из кэша.
                cur.execute('SELECT id FROM public.students')
                student_ids = [str(row[0]) for row in cur.fetchall()]

            # Сначала проверяем все подключения, потом начинаем удалять.
            phase = 'подключение Neo4j'
            driver = stack.enter_context(GraphDatabase.driver(
                f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}", auth=(NEO4J_USER, NEO4J_PASS),
                connection_timeout=5, connection_acquisition_timeout=5,
            ))
            # Проверяем доступ к Neo4j.
            driver.verify_connectivity()
            session = stack.enter_context(driver.session())
            phase = 'подключение Redis'
            cache = stack.enter_context(redis.Redis(
                host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASS,
                decode_responses=True, socket_connect_timeout=3, socket_timeout=3,
            ))
            # Проверяем доступ к Redis.
            cache.ping()
            phase = 'подключение MongoDB'
            mongo = stack.enter_context(MongoClient(
                host=MONGO_HOST, port=int(MONGO_PORT), username=MONGO_USER,
                password=MONGO_PASS, authSource='admin', serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000, socketTimeoutMS=5000,
            ))
            # Проверяем доступ к MongoDB.
            mongo.admin.command('ping')
            phase = 'подключение Elasticsearch'
            es = stack.enter_context(Elasticsearch(
                [f"http://{ES_HOST_IP}:{ES_PORT}"], basic_auth=(ES_USER, ES_PASS),
                request_timeout=5, max_retries=0,
            ))
            # Проверяем доступ к Elasticsearch.
            es.info()

            phase = 'очистка Redis'
            for offset in range(0, len(student_ids), 1000):
                # Удаляем из кэша только UUID старых студентов.
                cache.delete(*student_ids[offset:offset + 1000])
            phase = 'очистка PostgreSQL'
            # CASCADE не используем: чужие зависимости должны остановить удаление.
            # Удаляем таблицы генератора и партиции посещений.
            cur.execute('''
                DROP TABLE IF EXISTS public.attendance, public.schedule, public.course_materials,
                    public.lectures, public.classes, public.courses, public.students, public.groups,
                    public.department_specialties, public.specialities, public.departments,
                    public.institutes, public.universities;
            ''')
            phase = 'очистка Neo4j'
            # Удаляем узлы с метками проекта и их связи. Ограничения не трогаем.
            session.run('''
                MATCH (n)
                WHERE n:Student OR n:Group OR n:Course OR n:Lecture OR n:Schedule
                DETACH DELETE n
            ''').consume()
            phase = 'очистка MongoDB'
            # Удаляем коллекцию иерархии вузов. Остальные коллекции не трогаем.
            mongo[MONGO_DB]['university_hierarchy'].drop()
            phase = 'очистка Elasticsearch'
            # После прошлой очистки индекса может уже не быть.
            if es.indices.exists(index='course_materials'):
                # Удаляем индекс материалов генератора.
                es.indices.delete(index='course_materials')
            phase = 'завершение транзакции и закрытие соединений'
    except Exception:
        raise RuntimeError(f'Сброс не завершён: {phase}; возможна частичная очистка') from None
    print('Очистка артефактов генератора завершена!')


if __name__ == "__main__":
    try:
        run_undo()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None