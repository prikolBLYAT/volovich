#!/usr/bin/env bash
set -uo pipefail

# ==============================================================================
# 2.1 — REDIS (Кэш / Key-Value Хранилище)
# ==============================================================================

# Добавление данных (Запись 5 студентов группы через Hash)
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0133 surname "Новожилов" first "Евгений" group "БСБО-16-23" status "active"
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0128 surname "Иванов" first "Иван" group "БСБО-16-23" status "active"
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0129 surname "Петрова" first "Мария" group "БСБО-16-23" status "active"
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0131 surname "Сидоров" first "Пётр" group "БСБО-16-23" status "active"
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0135 surname "Орлова" first "Анна" group "БСБО-16-23" status "active"

# Чтение данных по ключу
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HGETALL students:23Б0133
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HGET students:23Б0129 surname

# Модификация данных (Обновление полей)
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0135 status "warning"
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning HSET students:23Б0135 last_update "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Удаление отдельных записей
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning DEL students:23Б0131 students:23Б0135

# Полное очищение (Удаление namespace/всех ключей)
docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning --scan pattern 'students:*' | xargs -r docker exec redis redis-cli -a 'superpassword123!' --no-auth-warning DEL

# ==============================================================================
# 2.2 — MONGODB (Документоориентированная БД)
# ==============================================================================

# Добавление данных (Вставка одного документа с вложенными массивами)
docker exec -it mongodb mongosh -u admin -p 'superpassword123!' --authenticationDatabase admin labdb --eval '
db.groups.insertOne({
  group_name: "БСБО-16-23",
  enrollment_year: 2023,
  specialty: "Безопасность программных решений",
  students: [
    { surname: "Новожилов", first: "Евгений", card: "23Б0133" },
    { surname: "Иванов", first: "Иван", card: "23Б0128" },
    { surname: "Петрова", first: "Мария", card: "23Б0129" },
    { surname: "Сидоров", first: "Пётр", card: "23Б0131" },
    { surname: "Орлова", first: "Анна", card: "23Б0135" }
  ],
  courses: [
    { name: "Проектирование архитектуры ПО", hours: 144 },
    { name: "Базы данных", hours: 72 },
    { name: "Операционные системы", hours: 54 },
    { name: "Машинное обучение", hours: 72 }
  ]
})
'

# Чтение данных (Поиск по названию группы)
docker exec -it mongodb mongosh -u admin -p 'superpassword123!' --authenticationDatabase admin labdb --eval '
db.groups.findOne({ group_name: "БСБО-16-23" }, { group_name: 1, students: 1, courses: 1 })
'

# Модификация данных (Добавление элемента в массив $push)
docker exec -it mongodb mongosh -u admin -p 'superpassword123!' --authenticationDatabase admin labdb --eval '
db.groups.updateOne(
  { group_name: "БСБО-16-23" },
  { $push: { students: { surname: "Кузнецова", first: "Дарья", card: "23Б0140" } } }
)
'

# Удаление данных из документа (Извлечение из массива $pull)
docker exec -it mongodb mongosh -u admin -p 'superpassword123!' --authenticationDatabase admin labdb --eval '
db.groups.updateOne(
  { group_name: "БСБО-16-23" },
  { $pull: { students: { card: "23Б0140" } } }
)
'

# Полное очищение (Удаление коллекции)
docker exec -it mongodb mongosh -u admin -p 'superpassword123!' --authenticationDatabase admin labdb --eval '
db.groups.drop()
'

# ==============================================================================
# 2.3 — NEO4J (Графовая БД)
# ==============================================================================

# Добавление данных (Создание узлов Group, Student, Course и связей MEMBER_OF, TAKES)
docker exec neo4j cypher-shell -u neo4j -p 'superpassword123!' '
CREATE (g:`Group` {name: "БСБО-16-23"})
WITH g
CREATE (s:`Student` {surname: "Новожилов", card: "23Б0133"})-[:`MEMBER_OF`]->(g)
WITH g, s
CREATE (s2:`Student` {surname: "Иванов", card: "23Б0128"})-[:`MEMBER_OF`]->(g)
WITH g, s, s2
CREATE (c:`Course` {name: "Базы данных", elective: false})
WITH g, s, s2, c
CREATE (c2:`Course` {name: "Машинное обучение", elective: true})
WITH s, c, c2
CREATE (s)-[:`TAKES` {mark: 82}]->(c)
WITH s, c2
CREATE (s)-[:`TAKES` {mark: 75}]->(c2);
'

# Чтение данных (Обход связей графа)
docker exec neo4j cypher-shell -u neo4j -p 'superpassword123!' '
MATCH (s:`Student` {card: "23Б0133"})-[r:`TAKES`]->(c:`Course`)
RETURN s.surname AS student, c.name AS course, c.elective AS elective, r.mark AS mark;
'

# Модификация данных (Обновление свойств связи и узла)
docker exec neo4j cypher-shell -u neo4j -p 'superpassword123!' '
MATCH (s:`Student` {card: "23Б0133"})-[r:`TAKES`]->(c:`Course` {name: "Машинное обучение"})
SET r.mark = 88, c.elective = false
RETURN c.name AS course, c.elective, r.mark;
'

# Удаление отдельных данных (Связи и отдельного узла)
docker exec neo4j cypher-shell -u neo4j -p 'superpassword123!' '
MATCH (s:`Student` {surname: "Иванов"})-[r:`MEMBER_OF`]->(:`Group`)
DELETE r;
MATCH (c:`Course` {name: "Базы данных"})
DETACH DELETE c;
'

# Полное очищение (Удаление всех узлов и связей)
docker exec neo4j cypher-shell -u neo4j -p 'superpassword123!' '
MATCH (n) DETACH DELETE n;
'

# ==============================================================================
# 2.4 — ELASTICSEARCH (Поисковый движок)
# ==============================================================================

# Создание индекса (Схема/Mapping)
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X PUT "http://localhost:9200/course_catalog" -d '{
  "mappings": {
    "properties": {
      "course_name": { "type": "text" },
      "description": { "type": "text" },
      "lecturer":    { "type": "text" },
      "group":       { "type": "keyword" },
      "semester":    { "type": "integer" }
    }
  }
}'

# Добавление данных (Индексация 3 документов)
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X PUT "http://localhost:9200/course_catalog/_doc/1?refresh" -d '{
  "course_name": "Базы данных",
  "description": "Реляционные СУБД, SQL, нормализация, транзакции, индексы",
  "lecturer":    "Смирнова А.В.",
  "group":       "БСБО-16-23",
  "semester":    5
}'
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X PUT "http://localhost:9200/course_catalog/_doc/2?refresh" -d '{
  "course_name": "Машинное обучение",
  "description": "Нейронные сети, обучение с подкреплением, градиентный спуск",
  "lecturer":    "Петрова М.И.",
  "group":       "БСБО-16-23",
  "semester":    5
}'
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X PUT "http://localhost:9200/course_catalog/_doc/3?refresh" -d '{
  "course_name": "Проектирование архитектуры ПО",
  "description": "Архитектура микросервисов, паттерны проектирования, API-дизайн",
  "lecturer":    "Волович М.А.",
  "group":       "БСБО-16-23",
  "semester":    5
}'

# Чтение и полнотекстовый поиск
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -X GET "http://localhost:9200/course_catalog/_doc/1"
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X POST "http://localhost:9200/course_catalog/_search" -d '{
  "query": { "match": { "description": "градиентный спуск" } }
}'

# Модификация данных (Перезапись документа)
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -H 'Content-Type: application/json' -X PUT "http://localhost:9200/course_catalog/_doc/1?refresh" -d '{
  "course_name": "Базы данных",
  "description": "Реляционные СУБД, SQL, нормализация, транзакции, индексы",
  "lecturer":    "Новожилов Е.И.",
  "group":       "БСБО-16-23",
  "semester":    5
}'

# Удаление отдельных документов
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -X DELETE "http://localhost:9200/course_catalog/_doc/2?refresh"
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -X DELETE "http://localhost:9200/course_catalog/_doc/3?refresh"

# Полное очищение (Удаление индекса)
docker exec elasticsearch curl -s -u 'elastic:superpassword123!' -X DELETE "http://localhost:9200/course_catalog"

# ==============================================================================
# 2.5 — POSTGRESQL (Реляционная БД)
# ==============================================================================

# Создание секционированной таблицы (Партиционирование по диапазону)
docker exec postgres psql -U admin -d db -X -c "
CREATE TABLE IF NOT EXISTS attendance (
  id           UUID         NOT NULL DEFAULT gen_random_uuid(),
  student_card VARCHAR(20)  NOT NULL,
  course_name  VARCHAR(200) NOT NULL,
  week_num     INTEGER      NOT NULL,
  attended     BOOLEAN      NOT NULL DEFAULT TRUE,
  visited_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
  PRIMARY KEY (id, week_num)
) PARTITION BY RANGE (week_num);

CREATE TABLE attendance_w1 PARTITION OF attendance FOR VALUES FROM (1) TO (2);
CREATE TABLE attendance_w2 PARTITION OF attendance FOR VALUES FROM (2) TO (3);
CREATE TABLE attendance_w3 PARTITION OF attendance FOR VALUES FROM (3) TO (4);
CREATE TABLE attendance_w4 PARTITION OF attendance FOR VALUES FROM (4) TO (5);
CREATE TABLE attendance_w5 PARTITION OF attendance FOR VALUES FROM (5) TO (6);
CREATE TABLE attendance_w6 PARTITION OF attendance FOR VALUES FROM (6) TO (7);
CREATE TABLE attendance_w7 PARTITION OF attendance FOR VALUES FROM (7) TO (8);
CREATE TABLE attendance_w8 PARTITION OF attendance FOR VALUES FROM (8) TO (9);
"

# Добавление данных (Генерация последовательностей)
docker exec postgres psql -U admin -d db -X -c "
INSERT INTO attendance (student_card, course_name, week_num, attended)
SELECT '23Б0133', v.course, n, random() < 0.8
FROM (VALUES ('Базы данных'), ('Операционные системы'), ('Машинное обучение')) v(course)
CROSS JOIN generate_series(1, 8) AS n;
"

# Чтение данных (Выборка по ключу партиционирования)
docker exec postgres psql -U admin -d db -X -c "
SELECT student_card, course_name, week_num, attended, visited_at
FROM attendance
WHERE student_card = '23Б0133' AND week_num = 1
ORDER BY course_name;
"

# Модификация данных (Обновление записи)
docker exec postgres psql -U admin -d db -X -c "
UPDATE attendance
SET attended = TRUE
WHERE student_card = '23Б0133'
  AND course_name  = 'Машинное обучение'
  AND week_num     = 3;
"

# Удаление отдельных строк (По атрибуту недели)
docker exec postgres psql -U admin -d db -X -c "
DELETE FROM attendance WHERE student_card = '23Б0133' AND week_num = 7;
"

# Полное очищение (Каскадное удаление таблицы со всеми партициями)
docker exec postgres psql -U admin -d db -X -c "
DROP TABLE IF EXISTS attendance CASCADE;
"
