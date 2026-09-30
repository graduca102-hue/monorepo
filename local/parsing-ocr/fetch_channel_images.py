import json, os, urllib.request, urllib.error, socket, time
from pathlib import Path
socket.setdefaulttimeout(25)
BASE = Path(__file__).resolve().parents[2]
for raw in (BASE/".env").read_text(encoding="utf-8-sig").splitlines():
    line=raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip())
TOKEN=os.environ["BOT_TOKEN"]; OWNER=int(os.environ["OWNER_ID"])
API=f"https://api.telegram.org/bot{TOKEN}/"
CHAT=-1003851717515
def call(method,**p):
    for attempt in range(4):
        data=json.dumps(p).encode()
        req=urllib.request.Request(API+method,data=data,headers={"Content-Type":"application/json"})
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            j=json.load(e)
            ra=j.get("parameters",{}).get("retry_after")
            if ra: time.sleep(ra+1); continue
            return j
        except Exception as ex:
            time.sleep(2); continue
    return {"ok":False,"description":"retries exhausted"}

def download(url,dest):
    for _ in range(3):
        try:
            with urllib.request.urlopen(url) as r, open(dest,"wb") as f:
                f.write(r.read())
            return True
        except Exception:
            time.sleep(2)
    return False

imgdir=Path("img")
have={int(p.stem[3:]) for p in imgdir.glob("msg*.jpg")}
new=0; miss=0
for mid in range(150,250):
    if mid in have: continue
    fr=call("forwardMessage",chat_id=OWNER,from_chat_id=CHAT,message_id=mid)
    if not fr.get("ok"):
        d=fr.get("description","")
        print(mid,"skip",d,flush=True)
        if "not found" in d:
            miss+=1
            if miss>=25: print("stop",flush=True); break
        continue
    miss=0
    m=fr["result"]; fmid=m["message_id"]
    photo=m.get("photo")
    if photo:
        f=call("getFile",file_id=photo[-1]["file_id"])
        if f.get("ok"):
            path=f["result"]["file_path"]
            if download(f"https://api.telegram.org/file/bot{TOKEN}/{path}", imgdir/f"msg{mid:03d}.jpg"):
                new+=1; print(mid,"ok",flush=True)
            cap=m.get("caption")
            if cap:(imgdir/f"msg{mid:03d}.caption.txt").write_text(cap,encoding="utf-8")
    else:
        print(mid,"non-image",list(m.keys()),flush=True)
    call("deleteMessage",chat_id=OWNER,message_id=fmid)
    time.sleep(0.4)
print("NEW",new,"TOTAL",len(list(imgdir.glob('msg*.jpg'))),flush=True)
