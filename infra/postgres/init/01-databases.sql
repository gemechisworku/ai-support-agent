-- Runs once on first start of the postgres container (docker-entrypoint-initdb.d) and in CI.
-- bank: mock core bank + interaction tables.  kb: policy knowledge base (pgvector).
-- *_test databases are used by integration tests.
CREATE DATABASE bank;
CREATE DATABASE kb;
CREATE DATABASE bank_test;
CREATE DATABASE kb_test;

\connect kb
CREATE EXTENSION IF NOT EXISTS vector;
\connect kb_test
CREATE EXTENSION IF NOT EXISTS vector;
