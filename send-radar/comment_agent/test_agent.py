import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
import store
import youtube

class Pipeline(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        self.db=store.connect(self.dir.name);self.addCleanup(self.db.close)
        with self.db:store.put(self.db,"channel","owner")
    def comment(self,cid="c",body="Could a submarine float on a brine pool?"):
        with self.db:store.ingest(self.db,cid=cid,root="c",video="v",title="Brine",body=body,seen=store.stamp())
        return self.db.execute("SELECT revision FROM comments WHERE id=?",(cid,)).fetchone()[0]
    def draft(self):
        rev=self.comment()
        store.apply(self.db,{"comments":[dict(id="c",revision=rev,draft="An underwater parking question!",new_topic="Can a sub float on brine?")]})
        return rev
    def test_replay_grouping_and_brief(self):
        rev=self.draft();i=self.db.execute("SELECT id FROM ideas").fetchone()[0]
        rev2=self.comment("nested","What if a sub landed in it?")
        p={"comments":[dict(id="nested",revision=rev2,idea_id=i,draft="Another question!")]}
        store.apply(self.db,p);store.apply(self.db,p)
        self.assertEqual(self.db.execute("SELECT count(*) FROM comments WHERE state='draft'").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM comments WHERE idea=?",(i,)).fetchone()[0],2)
        store.apply(self.db,{"research":[dict(id=i,stage="ready",evidence="Sample observation",source="https://example.org/report",footage="https://example.org/clip",rights="Example permission",detail="Sample visible edge")]})
        self.assertTrue(store.brief(self.db,i,8,self.dir.name).exists())
        self.assertEqual(store.pending(self.db)["research"],[])
    def test_edit_invalidates_old_draft(self):
        rev=self.draft(); self.comment(body="Changed question")
        store.apply(self.db,{"comments":[dict(id="c",revision=rev,draft="stale")]})
        self.assertEqual(self.db.execute("SELECT state FROM comments").fetchone()[0],"none")
        self.assertEqual(len(store.pending(self.db)["comments"]),1)
    def test_timeout_is_never_reposted(self):
        rev=self.draft()
        def fake(method,resource,token,params,data=None):
            if resource=="channels": return {"items":[{"id":"owner"}]}
            if method=="POST":raise TimeoutError("network")
            return {"items":[{"snippet":{"videoId":"v","channelId":"owner","textOriginal":"Could a submarine float on a brine pool?"}}]}
        with patch.object(youtube,"channel_lock",nullcontext),patch.object(youtube,"saved_token",return_value="sample"),patch.object(youtube,"pages",return_value=[]),patch.object(youtube,"fetch",side_effect=fake):
            with self.assertRaises(TimeoutError):youtube.publish(self.db,"c",rev,"Edited by owner")
            with self.assertRaises(ValueError):youtube.publish(self.db,"c",rev,"Edited by owner")
        self.assertEqual(tuple(self.db.execute("SELECT state,reply FROM comments").fetchone()),("verify","Edited by owner"))
    def test_reply_expansion_owner_and_deletion(self):
        self.comment("gone");self.draft()
        def mk(cid,user):
            return {"id":cid,"snippet":{"textDisplay":"hello "+cid,"authorChannelId":{"value":user}}}
        thread={"snippet":{"videoId":"v","channelId":"owner","totalReplyCount":2,"topLevelComment":mk("c","viewer")}}
        def listing(res,*args,**kw):return [thread] if res=="commentThreads" else [mk("q","v2"),mk("r","owner")]
        with patch.object(youtube,"pages",side_effect=listing),patch.object(youtube,"fetch",return_value={"items":[{"snippet":{"title":"Brine","channelId":"owner"}}]}):
            youtube.scan(self.db,"test","owner")
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT id,state FROM comments ORDER BY id")],[("c","answered"),("q","answered")])
    def test_partial_scan_preserves_unseen(self):
        self.comment("saved")
        with patch.object(youtube,"pages",side_effect=OSError("offline")):
            with self.assertRaises(OSError):youtube.scan(self.db,"token","owner")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM comments").fetchone()[0],1)
    def test_incomplete_evidence_cannot_promote(self):
        self.draft();iid=self.db.execute("SELECT id FROM ideas").fetchone()[0]
        with self.assertRaises(ValueError):store.brief(self.db,iid,12,self.dir.name)
        with self.assertRaises(ValueError):store.apply(self.db,{"research":[dict(id=iid,stage="ready",source="bad",footage="bad",rights="?",detail="?",evidence="")]})
    def test_approval_sends_exact_once(self):
        rev=self.draft();sent=[]
        def fake(method,resource,token,params,data=None):
            if resource=="channels":return {"items":[{"id":"owner"}]}
            if method=="POST":sent.append(data["snippet"]);return {"id":"receipt"}
            return {"items":[{"snippet":{"videoId":"v","channelId":"owner","textOriginal":"Could a submarine float on a brine pool?"}}]}
        with patch.object(youtube,"channel_lock",nullcontext),patch.object(youtube,"saved_token",return_value="sample"),patch.object(youtube,"pages",return_value=[]),patch.object(youtube,"fetch",side_effect=fake):
            youtube.publish(self.db,"c",rev,"My approved words")
            with self.assertRaises(ValueError):youtube.publish(self.db,"c",rev,"My approved words")
        self.assertEqual(sent,[{"parentId":"c","textOriginal":"My approved words"}])
if __name__=="__main__": unittest.main()
