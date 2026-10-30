import os
from dotenv import load_dotenv

# Импорты драйверов СУБД
import psycopg2
from neo4j import GraphDatabase
import redis
from pymongo import MongoClient
from elasticsearch import Elasticsearch

# Загрузка переменных из .env
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")

# PostgreSQL
PG_HOST = os.getenv("POSTGRES_HOST", DB_HOST)
PG_PORT = os.getenv("POSTGRES_PORT", "5432")
PG_USER = os.getenv("POSTGRES_USER", "admin")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "superpassword123!")
PG_DB = os.getenv("POSTGRES_DB", "db")

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


def run_undo():
    print("Старт очистки данных во всех СУБД\n")

    # 1. PostgreSQL (Очистка таблиц с CASCADE)
    print("Очистка PostgreSQL")
    try:
        with psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB
        ) as pg_conn:
            with pg_conn.cursor() as pg_cur:
                pg_cur.execute("""
                    TRUNCATE universities, institutes, departments, specialities, 
                             department_specialties, groups, students, courses, 
                             lectures, schedule, attendance CASCADE;
                """)
        print("PostgreSQL очищена!")
    except Exception as e:
        print(f"   [!] Ошибка при очистке PostgreSQL: {e}")

    # 2. Neo4j (Удаление всех узлов и связей)
    print("Очистка Neo4j")
    try:
        with GraphDatabase.driver(
            f"bolt://{NEO4J_HOST}:{NEO4J_BOLT_PORT}",
            auth=(NEO4J_USER, NEO4J_PASS)
        ) as neo4j_driver:
            with neo4j_driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")
                constraints = session.run("SHOW CONSTRAINTS YIELD name").data()
                for c in constraints:
                    session.run(f"DROP CONSTRAINT `{c['name']}`")
        print("Neo4j очищена!")
    except Exception as e:
        print(f"   [!] Ошибка при очистке Neo4j: {e}")

    # 3. Redis (Очистка сгенерированных ключей)
    print("Очистка Redis")
    try:
        with redis.Redis(
            host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASS, decode_responses=True
        ) as r_client:
            keys_to_delete = r_client.keys("schedule:*") + r_client.keys("attendance:*")
            if keys_to_delete:
                r_client.delete(*keys_to_delete)
        print("Redis очищена!")
    except Exception as e:
        print(f"   [!] Ошибка при очистке Redis: {e}")

    # 4. MongoDB (Очистка коллекции)
    print("Очистка MongoDB")
    try:
        mongo_uri = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
        with MongoClient(mongo_uri) as mongo_client:
            mongo_db = mongo_client[MONGO_DB]
            mongo_db["course_materials"].drop()
        print("MongoDB очищена!")
    except Exception as e:
        print(f"   [!] Ошибка при очистке MongoDB: {e}")

    # 5. Elasticsearch (Удаление индекса)
    print("Очистка Elasticsearch")
    try:
        with Elasticsearch(
            [f"http://{ES_HOST_IP}:{ES_PORT}"],
            basic_auth=(ES_USER, ES_PASS)
        ) as es_client:
            if es_client.indices.exists(index="lectures"):
                es_client.indices.delete(index="lectures")
        print("Elasticsearch очищена!")
    except Exception as e:
        print(f"   [!] Ошибка при очистке Elasticsearch: {e}")

    print("\nПолная отката (undo) завершена!")


if __name__ == "__main__":
    run_undo()