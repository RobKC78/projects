"""Local review inbox. Only the desktop UI has a publishing entry point."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SendRadar" / "inbox"
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ideas(
 id TEXT PRIMARY KEY, topic TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'inbox',
 evidence TEXT DEFAULT '', source TEXT DEFAULT '', footage TEXT DEFAULT '',
 rights TEXT DEFAULT '', detail TEXT DEFAULT '', duration INTEGER, notice INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS comments(
 id TEXT PRIMARY KEY, parent TEXT NOT NULL, video TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, revision TEXT NOT NULL, touched INTEGER NOT NULL,
 classified INTEGER DEFAULT 0, reply TEXT DEFAULT '', state TEXT DEFAULT 'none',
 idea TEXT REFERENCES ideas(id), notified INTEGER DEFAULT 0, posted_id TEXT DEFAULT '');
"""

def connect(root=None):
    base = Path(root or ROOT)
    base.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(base / "inbox.sqlite3", timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db

def get(db, key, default=""):
    row = db.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return row[0] if row else default

def put(db, key, val):
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(val)))

def stamp():
    return int(time.time())

def link(video, comment):
    return "https://www.youtube.com/watch?" + urlencode({"v":video, "lc":comment})

def ingest(db, *, cid, root, video, title, body, seen):
    revision = hashlib.sha256(body.encode()).hexdigest()
    db.execute("""INSERT INTO comments(id,parent,video,title,body,revision,touched)
      VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      touched=excluded.touched,title=excluded.title,body=excluded.body,revision=excluded.revision,
      classified=CASE WHEN revision<>excluded.revision THEN 0 ELSE classified END,
      idea=CASE WHEN revision<>excluded.revision THEN NULL ELSE idea END,
      state=CASE WHEN revision<>excluded.revision AND state='draft' THEN 'none' ELSE state END,
      reply=CASE WHEN revision<>excluded.revision AND state='draft' THEN '' ELSE reply END,
      notified=CASE WHEN revision<>excluded.revision THEN 0 ELSE notified END""",
      (cid,root,video,title,body,revision,seen))

def pending(db, limit=20):
    rows = db.execute("""SELECT c.id,c.revision,c.title,c.body,c.parent,
      CASE WHEN c.parent<>c.id THEN p.body END AS thread_context
      FROM comments c LEFT JOIN comments p ON p.id=c.parent
      WHERE c.classified=0 ORDER BY c.rowid LIMIT ?""",(limit,))
    return {"connected":bool(get(db,"channel")), "comments":[dict(r) for r in rows],
            "ideas":[dict(r) for r in db.execute("SELECT id,topic,stage FROM ideas ORDER BY rowid DESC LIMIT 150")],
            "research":[dict(r) for r in db.execute("SELECT id,topic FROM ideas WHERE stage='inbox' ORDER BY rowid LIMIT 8")]}

def short(value, limit):
    if not isinstance(value,str) or len(value)>limit: raise ValueError("Text field missing or too long")
    return value.strip()

def url(value):
    s=short(value,1600)
    u=urlsplit(s)
    if u.scheme!='https' or not u.hostname or u.username or u.password: raise ValueError("A public HTTPS source URL is required")
    return s

def apply(db, packet):
    """Atomic, revision-bound editorial import. Cannot approve or post replies."""
    with db:
        for item in packet.get("comments",[]):
            r=db.execute("SELECT * FROM comments WHERE id=? AND revision=? AND classified=0",
                         (item["id"],item["revision"])).fetchone()
            if not r: continue
            idea=item.get("idea_id") or None
            if idea and not db.execute("SELECT 1 FROM ideas WHERE id=?",(idea,)).fetchone():
                raise ValueError("Unknown existing idea ID")
            topic=short(item.get("new_topic",""),220)
            if topic and idea: raise ValueError("Choose an existing idea or a new topic")
            if topic:
                found=db.execute("SELECT id FROM ideas WHERE topic=? COLLATE NOCASE",(topic,)).fetchone()
                idea=found[0] if found else uuid.uuid4().hex
                if not found: db.execute("INSERT INTO ideas(id,topic) VALUES(?,?)",(idea,topic))
            reply=short(item.get("draft",""),600)
            blocked=db.execute("""SELECT 1 FROM comments WHERE parent=? AND state IN ('posted','sending','verify','answered','draft')""",(r["parent"],)).fetchone()
            if blocked or r["state"] in ('dismissed','posted','verify','sending','answered'): reply=""
            state="draft" if reply else r["state"]
            db.execute("UPDATE comments SET classified=1,idea=?,reply=CASE WHEN ?<>'' THEN ? ELSE reply END,state=? WHERE id=?",
                       (idea,reply,reply,state,r["id"]))
        for item in packet.get("research",[]):
            stage=item["stage"]
            if stage not in ("ready","needs_evidence"): raise ValueError("Invalid research decision")
            source=url(item["source"]) if stage=="ready" else ""
            footage=url(item["footage"]) if stage=="ready" else ""
            evidence=short(item.get("evidence",""),1600)
            rights=short(item.get("rights",""),900)
            detail=short(item.get("detail",""),900)
            if not evidence or (stage=="ready" and not (detail and rights)): raise ValueError("Incomplete research")
            db.execute("""UPDATE ideas SET stage=?,source=?,footage=?,evidence=?,rights=?,detail=?,notice=0
                WHERE id=? AND stage='inbox'""",(stage,source,footage,evidence,rights,detail,item["id"]))

def expire(db):
    with db: db.execute("DELETE FROM comments WHERE touched<?",(stamp()-28*86400,))

def brief(db, iid, minutes, folder):
    if not 1<=minutes<=240: raise ValueError("Choose a runtime from 1 to 240 minutes")
    r=db.execute("SELECT * FROM ideas WHERE id=? AND stage IN ('ready','production')",(iid,)).fetchone()
    if not r: raise ValueError("Select a researched idea first")
    sources=[link(x[0],x[1]) for x in db.execute("SELECT video,id FROM comments WHERE idea=?", (iid,))]
    data={"series":"Send Radar","question":r["topic"],"runtime_minutes":minutes,"evidence":r["evidence"],
          "primary_source":r["source"],"footage":r["footage"],"rights_and_credit":r["rights"],
          "visible_detail":r["detail"],"viewer_comment_links":sources,"viewer_question":"paraphrased",
          "production_request":"Use the Send Radar production guide. Deliver an episode and self-contained Shorts; review rights before rendering."}
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/("Send-Radar-"+iid+".json")
    tmp=folder/(path.name+"."+uuid.uuid4().hex+".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)
    with db: db.execute("UPDATE ideas SET stage='production',duration=? WHERE id=?",(minutes,iid))
    return path

@contextmanager
def channel_lock(directory=None):
    path=Path(directory or ROOT)
    path.mkdir(parents=True,exist_ok=True)
    with open(path/"channel.lock","a+b") as f:
        if not f.tell(): f.write(b"0"); f.flush()
        f.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
