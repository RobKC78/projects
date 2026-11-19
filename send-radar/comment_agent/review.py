import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from store import ROOT, brief, channel_lock, connect, expire, get, link, put
import youtube

def launch():
    window=tk.Tk();window.title("Send Radar  |  Comment inbox");window.geometry("880x660")
    window.minsize(760,570)
    style=ttk.Style();style.theme_use("clam")
    style.configure("TButton",padding=(10,6));style.configure("TNotebook.Tab",padding=(14,7))
    db=connect();expire(db)
    tasks=queue.Queue();busy=False; selected=None
    status=tk.StringVar();who=tk.StringVar()
    ttk.Label(window,text="Send Radar",font=("Segoe UI",22,"bold")).pack(anchor="w",padx=18,pady=(12,0))
    ttk.Label(window,textvariable=who).pack(anchor="w",padx=20,pady=(0,8))
    tabs=ttk.Notebook(window);tabs.pack(fill="both",expand=True,padx=14)
    reply,ideas,setup=[ttk.Frame(tabs,padding=12) for _ in range(3)]
    for page,title in ((reply,"Reply approvals"),(ideas,"Episode ideas"),(setup,"Setup")):tabs.add(page,text=title)
    ttk.Label(window,textvariable=status,wraplength=810).pack(anchor="w",padx=16,pady=7)
    def button(parent,label,command):
        w=ttk.Button(parent,text=label,command=command);w.pack(side="left",padx=(0,6));return w
    def background(fn,done,working):
        nonlocal busy
        if busy: return
        busy=True;status.set(working)
        def run():
            try: value=fn();tasks.put((done,value,None))
            except Exception as e: tasks.put((done,None,str(e)))
        threading.Thread(target=run,daemon=True).start()
    def poll():
        nonlocal busy
        try:
            cb,val,err=tasks.get_nowait();busy=False
            if err: status.set(err);messagebox.showerror("Send Radar",err)
            else: cb(val)
        except queue.Empty: pass
        finally: window.after(200,poll)
    def flag_error(e): messagebox.showerror("Send Radar",str(e))
    def writebox(widget,text,editable=False):
        widget.config(state="normal");widget.delete("1.0","end");widget.insert("1.0",text)
        if not editable: widget.config(state="disabled")
    def listbox(parent,columns):
        w=ttk.Treeview(parent,columns=columns,show="headings",height=6)
        for c in columns:w.heading(c,text=c);w.column(c,width=120 if c=="Status" else 420)
        w.pack(fill="both",expand=True,pady=(0,5));return w
    replies=listbox(reply,("Status","Video / comment"))
    ttk.Label(reply,text="Viewer comment").pack(anchor="w")
    comment=tk.Text(reply,height=3,wrap="word");comment.pack(fill="x")
    ttk.Label(reply,text="Radar’s reply · edit before posting").pack(anchor="w",pady=(6,0))
    editor=tk.Text(reply,height=3,wrap="word");editor.pack(fill="x")
    bar=ttk.Frame(reply);bar.pack(fill="x",pady=8)
    ilist=listbox(ideas,("Status","Episode question"))
    research=tk.Text(ideas,height=10,wrap="word");research.pack(fill="both",expand=True)
    ibar=ttk.Frame(ideas);ibar.pack(anchor="w",pady=7)
    def pick(tree):
        sel=tree.selection();return sel[0] if sel else None
    def select_reply(*args):
        nonlocal selected
        r=db.execute("SELECT * FROM comments WHERE id=?",(pick(replies),)).fetchone()
        selected=dict(r) if r else None
        writebox(comment,r["body"] if r else "")
        writebox(editor,r["reply"] if r else "",True)
    def select_idea(*args):
        r=db.execute("SELECT * FROM ideas WHERE id=?",(pick(ilist),)).fetchone()
        txt=""
        if r:
            refs=[link(x[0],x[1]) for x in db.execute("SELECT video,id FROM comments WHERE idea=?",(r["id"],))]
            txt="\n\n".join(x for x in [r["topic"],r["evidence"],r["source"],r["detail"],r["footage"],r["rights"],*refs] if x)
        writebox(research,txt)
    def refresh():
        who.set("Channel: "+(get(db,"name") or "Connect your channel in Setup"))
        for tree in [replies,ilist]:
            for x in tree.get_children():tree.delete(x)
        for r in db.execute("SELECT * FROM comments WHERE state IN ('draft','sending','verify') ORDER BY rowid"):
            replies.insert("","end",iid=r["id"],values=(r["state"],r["title"]+" · "+r["body"][:60]))
        for r in db.execute("SELECT * FROM ideas ORDER BY (stage='ready') DESC,rowid DESC"):
            ilist.insert("","end",iid=r["id"],values=(r["stage"],r["topic"]))
        for tree in [replies,ilist]:
            if tree.get_children():tree.selection_set(tree.get_children()[0])
        select_reply();select_idea()
        last=get(db,"last_scan")
        status.set(get(db,"last_error") or ("Last channel check: "+time.ctime(int(last)) if last else "Waiting for the first channel check."))
    def check():
        def work():
            conn=connect()
            try:
                result=youtube.sync(conn)
                with conn: put(conn,"last_error","")
                return result
            finally:conn.close()
        def done(result):refresh();status.set(result)
        background(work,done,"Checking YouTube…")
    def post():
        if busy or not selected or selected["state"]!="draft":return
        wording=editor.get("1.0","end-1c").strip();r=selected.copy()
        if not wording or not messagebox.askokcancel("Post to "+get(db,"name"),wording+"\n\nPublish this exact reply?"):return
        def send():
            conn=connect()
            try:return youtube.publish(conn,r["id"],r["revision"],wording)
            finally:conn.close()
        def done(msg):refresh();status.set(msg)
        background(send,done,"Sending your approved reply…")
    def skip():
        if selected and not busy:
            with db:db.execute("UPDATE comments SET state='dismissed' WHERE id=? AND state='draft'",(selected["id"],))
            refresh()
    def revisit():
        if not selected or busy:return
        webbrowser.open(link(selected["video"],selected["id"]))
    def reconcile():
        if busy or not selected or selected["state"] not in ("verify","sending"):return
        if not messagebox.askyesno("Recheck the thread","Check YouTube for a channel reply and restore this draft if none is visible? This check does not post."):return
        cid=selected["id"]
        def check_thread():
            conn=connect()
            try:return youtube.reconcile(conn,cid)
            finally:conn.close()
        def done(msg):refresh();status.set(msg)
        background(check_thread,done,"Checking the thread…")
    button(bar,"Recheck uncertain send",reconcile)
    button(bar,"Approve and post",post);button(bar,"Skip reply",skip);button(bar,"Open on YouTube",revisit);button(bar,"Refresh queue",refresh)
    def promote():
        iid=pick(ilist)
        if not iid:return
        r=db.execute("SELECT stage FROM ideas WHERE id=?",(iid,)).fetchone()
        if r[0] not in ("ready","production"):return messagebox.showinfo("Research pending","An evidence and footage check is needed first.")
        minutes=simpledialog.askinteger("Send Radar","How many minutes should this episode be?",minvalue=1,maxvalue=240)
        if minutes:
            try:
                where=brief(db,iid,minutes,get(db,"output",str(Path.home()/"Documents"/"Send Radar"/"Episode Briefs")))
                refresh();messagebox.showinfo("Episode brief saved",str(where))
            except Exception as e:flag_error(e)
    def research_again():
        iid=pick(ilist)
        if iid:
            with db:db.execute("UPDATE ideas SET stage='inbox' WHERE id=? AND stage='needs_evidence'",(iid,))
            refresh()
    button(ibar,"Approve for production…",promote);button(ibar,"Revisit research",research_again);button(ibar,"Refresh queue",refresh)
    replies.bind("<<TreeviewSelect>>",select_reply);ilist.bind("<<TreeviewSelect>>",select_idea)
    def row(label):
        frame=ttk.Frame(setup);frame.pack(fill="x",pady=3)
        ttk.Label(frame,text=label,wraplength=780,justify="left").pack(anchor="w",pady=(0,3))
        actions=ttk.Frame(frame);actions.pack(anchor="w");return actions
    button(row("1. Choose a Google Cloud project and enable YouTube Data API v3."),"Open YouTube API setup",
           lambda:webbrowser.open("https://console.cloud.google.com/apis/library/youtube.googleapis.com"))
    b=row("2. Set up the OAuth consent screen. For an external app in Testing, add your Google account as a test user. Create a Desktop app OAuth client and download its JSON.")
    button(b,"Configure audience",lambda:webbrowser.open("https://console.cloud.google.com/auth/audience"))
    button(b,"Create desktop client",lambda:webbrowser.open("https://console.cloud.google.com/auth/clients"))
    def auth():
        if busy:return
        path=filedialog.askopenfilename(title="Choose desktop client JSON",filetypes=[("Google client JSON","*.json")])
        if not path:return
        def done(result):
            cid,name,creds=result
            if get(db,"channel") and get(db,"channel")!=cid:
                return messagebox.showinfo("Different channel","Disconnect the old channel first or sign in with its original account.")
            if not messagebox.askyesno("Connect channel",f"Monitor and enable approved replies for {name}?\n{cid}"):return
            import keyring
            try:
                with channel_lock():
                    keyring.set_password(youtube.SERVICE,"oauth",json.dumps(creds))
                    with db:put(db,"channel",cid);put(db,"name",name)
            except Exception as e:return flag_error(e)
            refresh();check()
        background(lambda:youtube.sign_in(path),done,"Complete Google sign-in in the browser…")
    button(row("3. Select the JSON, sign in through Google, then choose the Send Radar channel."),"Connect or reconnect YouTube…",auth)
    b=row("4. The desktop checks every 30 minutes while you are signed in and the PC is awake. ChatGPT reviews new items separately. Notifications are for approvals.")
    button(b,"Check now",check)
    def outdir():
        path=filedialog.askdirectory(title="Where should approved episode briefs go?")
        if path:
            with db:put(db,"output",path)
            status.set("Production folder saved")
    button(b,"Choose production folder…",outdir)
    def disconnect():
        if busy or not messagebox.askyesno("Disconnect","Remove the local Google credential, comments, and queued ideas? Previously exported production briefs are separate."):return
        import keyring
        try:
            with channel_lock():
                try:keyring.delete_password(youtube.SERVICE,"oauth")
                except keyring.errors.PasswordDeleteError:pass
                with db:
                    for table in ("comments","ideas","settings"):db.execute("DELETE FROM "+table)
            webbrowser.open("https://myaccount.google.com/permissions");refresh()
        except Exception as e:flag_error(e)
    button(row("Google grants access to read comments and post the replies you approve. Disconnect to clear locally stored channel data."),"Disconnect…",disconnect)
    refresh()
    if not get(db,"channel"):tabs.select(setup)
    window.after(200,poll);window.mainloop()
