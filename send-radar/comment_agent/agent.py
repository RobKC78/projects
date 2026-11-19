"""Background transport and narrow editorial bridge. Run without arguments for desktop review."""
import argparse
import json
import os
import sys
from pathlib import Path

from store import ROOT, apply, connect, expire, get, pending, put, stamp
from youtube import sync

def approvals(db):
    rows=db.execute("SELECT COUNT(*) FROM comments WHERE state='draft' AND notified=0").fetchone()[0]
    ideas=db.execute("SELECT COUNT(*) FROM ideas WHERE stage='ready' AND notice=0").fetchone()[0]
    return rows,ideas

def notify(db):
    if os.name!="nt": return
    # Keep the transaction short and notify once for this batch.
    with db:
        db.execute("BEGIN IMMEDIATE")
        drafts,ideas=approvals(db)
        if not (drafts or ideas): return
        from winotify import Notification, audio
        toast=Notification(app_id="Send Radar",title="Send Radar needs your approval",
                        msg=f"{drafts} new reply draft(s) and {ideas} researched episode idea(s).",
                        launch="sendradar-review:open")
        toast.set_audio(audio.Silent,loop=False)
        toast.add_actions(label="Review in Send Radar",launch="sendradar-review:open")
        toast.show()
        db.execute("UPDATE comments SET notified=1 WHERE state='draft' AND notified=0")
        db.execute("UPDATE ideas SET notice=1 WHERE stage='ready' AND notice=0")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",nargs="?",default="review",choices=["review","sync","work","apply","status"])
    parser.add_argument("packet",nargs="?")
    a=parser.parse_args()
    if a.action=="review":
        from review import launch
        launch(); return
    db=connect();expire(db)
    if a.action=="sync":
        try:
            print(sync(db))
            with db: put(db,"last_error","")
        except Exception as e:
            with db: put(db,"last_error",str(e)[:600])
            raise
        finally: notify(db)
    elif a.action=="work": print(json.dumps(pending(db),ensure_ascii=True))
    elif a.action=="apply":
        # File path is owned by the local operator; the packet contains data only.
        if not a.packet: parser.error("apply requires the path to an editorial JSON packet")
        with open(a.packet,encoding="utf-8-sig") as f: packet=json.load(f)
        apply(db,packet)
        notify(db)
        print("Editorial work saved")
    else:
        print(json.dumps({"channel":get(db,"name"),"last_check":get(db,"last_scan"),"last_error":get(db,"last_error"),
          "pending_comments":db.execute("SELECT COUNT(*) FROM comments WHERE classified=0").fetchone()[0],
          "reply_drafts":db.execute("SELECT COUNT(*) FROM comments WHERE state='draft'").fetchone()[0],
          "ideas":db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]}))
if __name__=="__main__":
    main()
