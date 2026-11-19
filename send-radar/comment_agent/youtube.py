import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from store import get, put, stamp, ingest, channel_lock, expire

SCOPE="https://www.googleapis.com/auth/youtube.force-ssl"
SERVICE="Send Radar Comment Inbox"

class ApiError(Exception):
    def __init__(self,status,reason):
        super().__init__(f"YouTube {status}: {reason}")
        self.status=status

def fetch(method, resource, token, params, data=None):
    req=Request("https://www.googleapis.com/youtube/v3/"+resource+"?"+urlencode(params),
        method=method,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},
        data=json.dumps(data).encode() if data is not None else None)
    try:
        with urlopen(req,timeout=25) as resp: return json.load(resp)
    except HTTPError as e:
        try: reason=json.load(e)["error"]["message"]
        except Exception: reason=e.reason
        raise ApiError(e.code,reason) from None

def sign_in(client_path):
    from google_auth_oauthlib.flow import InstalledAppFlow
    with open(client_path,encoding="utf-8-sig") as f: client=json.load(f)
    if "installed" not in client: raise ValueError("Choose a Google Desktop app client JSON")
    flow=InstalledAppFlow.from_client_config(client,[SCOPE],autogenerate_code_verifier=True)
    creds=flow.run_local_server(port=0,host="127.0.0.1",prompt="consent select_account",
                                timeout_seconds=300,open_browser=True,authorization_prompt_message="")
    channels=fetch("GET","channels",creds.token,dict(part="snippet",mine="true"))["items"]
    if len(channels)!=1 or not creds.refresh_token:
        raise ValueError("Choose the Google/Brand Account for one YouTube channel and grant offline access")
    c=channels[0]
    return c["id"],c["snippet"]["title"],{k:getattr(creds,k) for k in ("refresh_token","client_id","client_secret")}

def saved_token(secret=None):
    import keyring
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as Refresh
    config=secret or json.loads(keyring.get_password(SERVICE,"oauth") or "null")
    if not config: raise ValueError("Open Setup and connect YouTube first")
    c=Credentials(token=None,token_uri="https://oauth2.googleapis.com/token",scopes=[SCOPE],**config)
    c.refresh(Refresh())
    return c.token

def pages(resource,token,**params):
    params.update(part=params.get("part","snippet"),maxResults=100)
    while True:
        result=fetch("GET",resource,token,params)
        yield from result.get("items",[])
        nxt=result.get("nextPageToken")
        if not nxt: break
        params["pageToken"]=nxt

def is_owner(comment,channel):
    return comment["snippet"].get("authorChannelId",{}).get("value")==channel

def sync(db):
    expire(db)
    channel=get(db,"channel")
    if not channel: return "Waiting for YouTube sign-in"
    with channel_lock():
        token=saved_token()
        if not any(x["id"]==channel for x in fetch("GET","channels",token,dict(part="id",mine="true"))["items"]):
            raise ValueError("Google account does not match the connected channel")
        return scan(db,token,channel)

def scan(db,token,channel):
    titles={}
    sweep=stamp()
    seen=set()
    for thread in pages("commentThreads",token,part="snippet,replies",allThreadsRelatedToChannelId=channel,
                        order="time",textFormat="plainText"):
        sn=thread["snippet"]; video=sn.get("videoId")
        if not video or sn.get("channelId")!=channel: continue
        if video not in titles:
            data=fetch("GET","videos",token,dict(part="snippet",id=video)).get("items",[])
            if not data or data[0]["snippet"].get("channelId")!=channel:
                titles[video]=None
            else: titles[video]=data[0]["snippet"]["title"]
        if titles[video] is None: continue
        top=sn["topLevelComment"]; root=top["id"]
        replies=thread.get("replies",{}).get("comments",[])
        if len(replies)<sn.get("totalReplyCount",0):
            replies=list(pages("comments",token,parentId=root,textFormat="plainText"))
        owner_replies=[x for x in replies if is_owner(x,channel)]
        with db:
            for c in [top,*replies]:
                if is_owner(c,channel): continue
                cid=c["id"]; body=c["snippet"].get("textOriginal",c["snippet"].get("textDisplay",""))
                ingest(db,cid=cid,root=root,video=video,title=titles[video],body=body,seen=sweep)
                seen.add(cid)
            if owner_replies:
                db.execute("""UPDATE comments SET state='answered' WHERE parent=? AND state IN ('none','draft','verify','sending')""",(root,))
    # Only a complete scan removes vanished comments. Retain a durable publication claim on uncertain writes.
    with db:
        for r in db.execute("SELECT id FROM comments").fetchall():
            if r["id"] not in seen: db.execute("DELETE FROM comments WHERE id=?",(r["id"],))
        put(db,"last_scan",stamp())
    return f"Checked {len(seen)} viewer comments"

def publish(db,cid,revision,wording):
    text=wording.strip()
    if not text or len(text)>600: raise ValueError("Reply must be 1–600 characters")
    with channel_lock():
        row=db.execute("SELECT * FROM comments WHERE id=?",(cid,)).fetchone()
        if not row or row["state"]!="draft" or row["revision"]!=revision: raise ValueError("Draft changed. Refresh the inbox")
        token=saved_token(); channel=get(db,"channel")
        if not any(x["id"]==channel for x in fetch("GET","channels",token,dict(part="id",mine="true"))["items"]):
            raise ValueError("Reconnect the correct channel in Setup")
        current=fetch("GET","comments",token,dict(part="snippet",id=cid,textFormat="plainText")).get("items",[])
        if not current: raise ValueError("This comment is no longer public")
        c=current[0]["snippet"]
        if (c.get("videoId")!=row["video"] or c.get("channelId")!=channel or
            c.get("textOriginal",c.get("textDisplay"))!=row["body"]):
            raise ValueError("The source comment changed. Run Check now before approving")
        if any(is_owner(x,channel) for x in pages("comments",token,parentId=row["parent"],textFormat="plainText")):
            with db: db.execute("UPDATE comments SET state='answered' WHERE parent=? AND state='draft'",(row["parent"],))
            return "This thread already has a channel reply"
        with db:
            n=db.execute("UPDATE comments SET reply=?,state='sending' WHERE id=? AND state='draft' AND revision=?",
                         (text,cid,revision)).rowcount
            if n!=1: raise ValueError("This reply has changed")
        try: result=fetch("POST","comments",token,dict(part="snippet"),{"snippet":{"parentId":row["parent"],"textOriginal":text}})
        except Exception as e:
            # A timeout can happen after YouTube accepted the post. Never auto-resend.
            state="draft" if isinstance(e,ApiError) and e.status in (400,401,403,404,429) else "verify"
            with db: db.execute("UPDATE comments SET state=? WHERE id=?",(state,cid))
            raise
        with db: db.execute("UPDATE comments SET state='posted',posted_id=? WHERE id=?",(result["id"],cid))
        return "Reply posted"

def reconcile(db,cid):
    """Only called after a creator checks the thread and requests recovery."""
    with channel_lock():
        r=db.execute("SELECT * FROM comments WHERE id=? AND state IN ('verify','sending')",(cid,)).fetchone()
        if not r: raise ValueError("Refresh the review queue")
        token=saved_token()
        exists=any(is_owner(x,get(db,"channel")) for x in pages("comments",token,parentId=r["parent"],textFormat="plainText"))
        with db:
            db.execute("UPDATE comments SET state=? WHERE id=?",("answered" if exists else "draft",cid))
        return "Existing channel reply found" if exists else "Restored for a new manual approval"
