import json, os, urllib.request, urllib.error, time
from pathlib import Path
BASE = Path(__file__).resolve().parents[2]
for raw in (BASE/".env").read_text(encoding="utf-8-sig").splitlines():
    line=raw.strip()
    if line and not line.startswith("#") and "=" in line:
        k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip())
TOKEN=os.environ["BOT_TOKEN"]; OWNER=int(os.environ["OWNER_ID"])
API=f"https://api.telegram.org/bot{TOKEN}/"
CHAT=-1003851717515
def call(method,**p):
    data=json.dumps(p).encode()
    req=urllib.request.Request(API+method,data=data,headers={"Content-Type":"application/json"})
    try: return json.load(urllib.request.urlopen(req,timeout=60))
    except urllib.error.HTTPError as e: return json.load(e)
imgdir=Path("img")
have={int(p.stem[3:]) for p in imgdir.glob("msg*.jpg")}
new=0; miss=0; forwarded=[]
for mid in range(227,340):
    fr=call("forwardMessage",chat_id=OWNER,from_chat_id=CHAT,message_id=mid)
    if not fr.get("ok"):
        d=fr.get("description","")
        print(mid,"skip",d,flush=True)
        if "not found" in d or "to forward not found" in d:
            miss+=1
            if miss>=20: print("stop 20 missing",flush=True); break
        time.sleep(0.2); continue
    miss=0
    m=fr["result"]; fmid=m["message_id"]; forwarded.append(fmid)
    photo=m.get("photo")
    if photo:
        f=call("getFile",file_id=photo[-1]["file_id"])
        if f.get("ok"):
            path=f["result"]["file_path"]
            urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{TOKEN}/{path}",
                                       imgdir/f"msg{mid:03d}.jpg")
            new+=1; print(mid,"IMG ok",flush=True)
            cap=m.get("caption")
            if cap:(imgdir/f"msg{mid:03d}.caption.txt").write_text(cap,encoding="utf-8")
    else:
        print(mid,"non-image",list(m.keys()),flush=True)
    time.sleep(0.3)
for fmid in forwarded: call("deleteMessage",chat_id=OWNER,message_id=fmid)
print("NEW",new,"TOTAL",len(list(imgdir.glob('msg*.jpg'))),flush=True)
