-- Shared-document tables exactly as live_genesis/documents.py created them before hash scheme 2
-- (main 7d3be7f), with two documents written by that code: #1 by agent keys, #2 scripted demo.
-- Every chain_hash below is a real scheme 1 hash of its row. Used by tests/test_documents.py.
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES agents(id),
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    created_at TEXT NOT NULL, head INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS document_revisions (
    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
    number INTEGER NOT NULL CHECK(number >= 1),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    base INTEGER NOT NULL CHECK(base >= 0 AND base < number),
    kind TEXT NOT NULL CHECK(kind IN ('doc_create','doc_revise','doc_revert')),
    reverted_to INTEGER, body TEXT NOT NULL, summary TEXT NOT NULL,
    size_delta INTEGER NOT NULL, lines_added INTEGER NOT NULL, lines_removed INTEGER NOT NULL,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)), created_at TEXT NOT NULL,
    content_hash TEXT NOT NULL, chain_hash TEXT NOT NULL,
    UNIQUE (document_id, number)
);
CREATE TRIGGER IF NOT EXISTS document_revisions_no_update BEFORE UPDATE ON document_revisions
    BEGIN SELECT RAISE(ABORT, 'document_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS document_revisions_no_delete BEFORE DELETE ON document_revisions
    BEGIN SELECT RAISE(ABORT, 'document_revisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS documents_no_delete BEFORE DELETE ON documents
    BEGIN SELECT RAISE(ABORT, 'documents cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS documents_fixed_fields BEFORE UPDATE OF id,title,topic,created_by,demo,created_at ON documents
    BEGIN SELECT RAISE(ABORT, 'document title, topic and origin are fixed'); END;
CREATE TRIGGER IF NOT EXISTS documents_head_steps BEFORE UPDATE OF head ON documents
    WHEN NEW.head <> OLD.head + 1
    BEGIN SELECT RAISE(ABORT, 'document head moves forward one revision at a time'); END;
-- Derived cache, safe to delete: per-line author ids of the head revision (see blame()).
CREATE TABLE IF NOT EXISTS document_blame (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    revision INTEGER NOT NULL, authors TEXT NOT NULL
);
INSERT INTO "document_blame" VALUES(1,2,'[1,2,1,2]');
INSERT INTO "document_blame" VALUES(2,2,'[3,3,3,1]');
INSERT INTO "document_revisions" VALUES(1,1,1,1,0,'doc_create',NULL,'alpha
beta
gamma','Created the document',16,3,0,0,'2026-11-12T18:29:53+00:00','f3220283d05d1ff2ae350cfe9e0e367cb5aef46e10efb203c8a53c678e2218c8','a2475ad9055e266474be8148af2ed6521408d088c70a03c2e72be5cda9a17173');
INSERT INTO "document_revisions" VALUES(2,1,2,2,1,'doc_revise',NULL,'alpha
beta revised
gamma
delta','Reworded beta and added delta',14,2,1,0,'2026-11-12T18:29:53+00:00','b61d57cc5bef4784a853f05a2e14f2fe4e8e4442dc798342b3b9b48b6f179731','137974c2f951688c00007d509a75781dc7273776ccfaf6d5d2099f1497d89971');
INSERT INTO "document_revisions" VALUES(3,2,1,3,0,'doc_create',NULL,'one
two
three','Created the document',13,3,0,1,'2026-11-12T18:29:53+00:00','058053d87c818d699cde0f00d670bca0e1c6ad857caa9758ea6a556d7c64fcee','2d81ddabc7cb8842b194fcfbf97c8df99448ec53dc17185fd4ec09f59684ed19');
INSERT INTO "document_revisions" VALUES(4,2,2,1,1,'doc_revise',NULL,'one
two
three
four','Scripted edit',5,1,0,1,'2026-11-12T18:29:53+00:00','c2fa7d384065e108cf8746af983dfe308f3cc709631df3f2615d450e1f07fd0d','e5acd96a4245810f467aa89e532d2978172c89d7cc19bf9a266bc84043038f1a');
INSERT INTO "documents" VALUES(1,'Field notes','community',1,0,'2026-11-12T18:29:53+00:00',2);
INSERT INTO "documents" VALUES(2,'Scripted sample','community',3,1,'2026-11-12T18:29:53+00:00',2);
