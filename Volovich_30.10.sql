CREATE TABLE "Universities" (
  "id" uuid PRIMARY KEY,
  "name" varchar,
  "short_name" varchar,
  "address" text,
  "website" varchar
);

CREATE TABLE "Institutes" (
  "id" uuid PRIMARY KEY,
  "university_id" uuid NOT NULL,
  "name" varchar,
  "short_name" varchar,
  "dean" varchar
);

CREATE TABLE "Departments" (
  "id" uuid PRIMARY KEY,
  "institute_id" uuid NOT NULL,
  "name" varchar,
  "short_name" varchar,
  "head" varchar
);

CREATE TABLE "Specialities" (
  "id" uuid PRIMARY KEY,
  "name" varchar,
  "code" varchar,
  "degree_level" varchar,
  "duration" int
);

CREATE TABLE "DepartmentSpecialties" (
  "id" uuid PRIMARY KEY,
  "department_id" uuid NOT NULL,
  "speciality_id" uuid NOT NULL
);

CREATE TABLE "Groups" (
  "id" uuid PRIMARY KEY,
  "speciality_id" uuid NOT NULL,
  "department_id" uuid NOT NULL,
  "name" varchar,
  "enrollment_year" int,
  "curator" varchar
);

CREATE TABLE "Students" (
  "id" uuid PRIMARY KEY,
  "group_id" uuid NOT NULL,
  "first_name" varchar,
  "second_name" varchar,
  "email" varchar,
  "phone" varchar,
  "student_card_number" varchar,
  "status" varchar
);

CREATE TABLE "Courses" (
  "id" uuid PRIMARY KEY,
  "speciality_id" uuid NOT NULL,
  "departmemt_id" uuid NOT NULL,
  "name" varchar,
  "description" text,
  "semester" int,
  "duration_hours" int,
  "lecture_hours" int,
  "practice_hours" int
);

CREATE TABLE "Lectures" (
  "id" uuid PRIMARY KEY,
  "course_id" uuid NOT NULL,
  "title" varchar,
  "technical_requirements" boolean,
  "annotation" text,
  "type" varchar
);

CREATE TABLE "CourseMaterial" (
  "id" uuid PRIMARY KEY,
  "lecture_id" uuid NOT NULL,
  "content_type" varchar,
  "title" varchar,
  "file_url" varchar,
  "metadata" json
);

CREATE TABLE "Schedule" (
  "id" uuid PRIMARY KEY,
  "class_id" uuid NOT NULL,
  "group_id" uuid NOT NULL,
  "schedule_date" date,
  "period" int,
  "room" varchar,
  "lecturer" varchar
);

CREATE TABLE "Attendance" (
  "id" uuid PRIMARY KEY,
  "schedule_id" uuid NOT NULL,
  "student_id" uuid NOT NULL,
  "date" date,
  "period" int,
  "mark" int,
  "marked_at" timestamp
);

ALTER TABLE "Institutes" ADD FOREIGN KEY ("university_id") REFERENCES "Universities" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Departments" ADD FOREIGN KEY ("institute_id") REFERENCES "Institutes" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "DepartmentSpecialties" ADD FOREIGN KEY ("department_id") REFERENCES "Departments" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Groups" ADD FOREIGN KEY ("speciality_id") REFERENCES "Specialities" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Groups" ADD FOREIGN KEY ("department_id") REFERENCES "Departments" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Students" ADD FOREIGN KEY ("group_id") REFERENCES "Groups" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Courses" ADD FOREIGN KEY ("speciality_id") REFERENCES "Specialities" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Lectures" ADD FOREIGN KEY ("course_id") REFERENCES "Courses" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "CourseMaterial" ADD FOREIGN KEY ("lecture_id") REFERENCES "Lectures" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Schedule" ADD FOREIGN KEY ("group_id") REFERENCES "Groups" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Schedule" ADD FOREIGN KEY ("class_id") REFERENCES "Lectures" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Attendance" ADD FOREIGN KEY ("schedule_id") REFERENCES "Schedule" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Attendance" ADD FOREIGN KEY ("student_id") REFERENCES "Students" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "Courses" ADD FOREIGN KEY ("departmemt_id") REFERENCES "Departments" ("id") DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "DepartmentSpecialties" ADD FOREIGN KEY ("speciality_id") REFERENCES "Specialities" ("id") DEFERRABLE INITIALLY IMMEDIATE;
