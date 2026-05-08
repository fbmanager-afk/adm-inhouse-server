import os,json,re,base64,urllib.request,urllib.error
from datetime import datetime
from http.server import HTTPServer,BaseHTTPRequestHandler

GITHUB_TOKEN=os.environ.get("GITHUB_TOKEN","")
GITHUB_USER=os.environ.get("GITHUB_USER","")
GITHUB_REPO=os.environ.get("GITHUB_REPO","adm-intel")
ANTHROPIC_KEY=os.environ.get("ANTHROPIC_API_KEY","")
PORT=int(os.environ.get("PORT",8080))

def call_claude(pdf_b64, prompt):
    payload={"model":"claude-haiku-4-5-20251001","max_tokens":2048,"messages":[{"role":"user","content":[{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":pdf_b64}},{"type":"text","text":prompt}]}]}
    req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=json.dumps(payload).encode(),headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=90) as r:
        resp=json.loads(r.read())
    raw=resp["content"][0]["text"].strip()
    raw=re.sub(r"^```json\s*","",raw)
    raw=re.sub(r"\s*```$","",raw)
    raw=re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]","",raw)
    return raw

def extract_with_claude(pdf_bytes):
    print("PDF to Claude "+str(len(pdf_bytes)//1024)+"KB")
    pdf_b64=base64.standard_b64encode(pdf_bytes).decode()

    stats_prompt="From this Hotel Arenas del Mar In-House Report, extract the summary stats. Return ONLY this JSON: {\"fecha\":\"DD/MM/YYYY\",\"ocupadas\":0,\"total_hab\":37,\"adultos\":0,\"ninos\":0,\"total_pax\":0,\"pct_ocupacion\":0.0,\"mod\":\"\",\"ayb\":\"\",\"concierge\":\"\",\"actividades\":[]}"
    stats_raw=call_claude(pdf_b64, stats_prompt)
    print("Stats: "+str(len(stats_raw))+" chars")
    try:
        stats=json.loads(stats_raw)
    except:
        stats={"fecha":datetime.now().strftime("%d/%m/%Y"),"ocupadas":0,"total_hab":37,"adultos":0,"ninos":0,"total_pax":0,"pct_ocupacion":0,"mod":"","ayb":"","concierge":"","actividades":[]}

    guests_prompt="From this Hotel Arenas del Mar In-House Report, list ALL rooms. Return ONLY a JSON array. Each item: {\"hab\":\"101A\",\"nombre\":\"LastName FirstName\",\"pax\":2,\"entrada\":\"DD/MM\",\"salida\":\"DD/MM\",\"checkout_hoy\":false,\"checkin_hoy\":false,\"alergias\":[],\"cortesia\":false,\"vip\":false}. checkin_hoy=true if room has asterisk or entrada=today. checkout_hoy=true if salida=today. Use only ASCII in strings. Return ONLY the JSON array."
    guests_raw=call_claude(pdf_b64, guests_prompt)
    print("Guests raw: "+str(len(guests_raw))+" chars")
    guests_raw=re.sub(r"[\x80-\xff]","",guests_raw)
    try:
        raw_guests=json.loads(guests_raw)
    except json.JSONDecodeError as e:
        print("JSON error: "+str(e))
        m=re.search(r'\[.*\]',guests_raw,re.DOTALL)
        if m:
            try:raw_guests=json.loads(m.group())
            except:raw_guests=[]
        else:raw_guests=[]
    print("Guests parsed: "+str(len(raw_guests)))
    stats["guests"]=raw_guests
    return stats

def classify_seg(alergias,vip):
    al=" ".join(alergias).lower()
    if vip:return"vip"
    if any(k in al for k in["vegetarian","celiac","gluten"]):return"wellness"
    return"nomad"

def build_guests(raw_guests):
    guests=[]
    for g in raw_guests:
        try:
            al=g.get("alergias",[])
            if not isinstance(al,list):al=[]
            seg=classify_seg(al,bool(g.get("vip",False)))
            alerts=[]
            if g.get("checkout_hoy"):alerts.append("CHECKOUT HOY")
            if g.get("checkin_hoy"):alerts.append("CHECK-IN HOY")
            if g.get("cortesia"):alerts.append("CORTESIA")
            if g.get("vip"):alerts.append("VIP")
            hab=str(g.get("hab","")).replace("*","").strip()
            guests.append({"h":hab,"n":str(g.get("nombre","")).title(),"pax":max(1,int(g.get("pax",2) or 2)),"in":str(g.get("entrada","")),"out":str(g.get("salida","")),"seg":seg,"checkout":bool(g.get("checkout_hoy",False)),"checkin":bool(g.get("checkin_hoy",False)),"cortesia":bool(g.get("cortesia",False)),"vip":bool(g.get("vip",False)),"lco":None,"dietary":[str(a) for a in al[:4]],"alerts":alerts[:5],"obs":""})
        except Exception as ex:
            print("Guest error: "+str(ex))
    return guests

def inject_html(html,data,guests):
    fecha=str(data.get("fecha",""))
    if fecha:html=re.sub(r"date:'[^']*'","date:'"+fecha+"'",html,count=1)
    occ=data.get("pct_ocupacion",0)
    if occ:html=re.sub(r"occ:[\d.]+,","occ:"+str(occ)+",",html,count=1)
    ocup=data.get("ocupadas",0)
    if ocup:html=re.sub(r"ocupadas:\d+,","ocupadas:"+str(ocup)+",",html,count=1)
    adu=data.get("adultos",0)
    if adu:html=re.sub(r"adultos:\d+,","adultos:"+str(adu)+",",html,count=1)
    nin=data.get("ninos",0)
    html=re.sub(r"ninos:\d+,","ninos:"+str(nin)+",",html,count=1)
    tpax=data.get("total_pax",0)
    if tpax:html=re.sub(r"total_pax:\d+,","total_pax:"+str(tpax)+",",html,count=1)
    mod=str(data.get("mod",""))
    if mod:html=re.sub(r"MOD:'[^']*'","MOD:'"+mod+"'",html,count=1)
    ayb=str(data.get("ayb",""))
    if ayb:html=re.sub(r"AyB:'[^']*'","AyB:'"+ayb+"'",html,count=1)
    acts=json.dumps(data.get("actividades",[]),ensure_ascii=True)
    html=re.sub(r"actividades:\[[^\]]*\]","actividades:"+acts,html,count=1)
    if guests:
        gj=json.dumps(guests,ensure_ascii=True)
        html=re.sub(r"(guests:\s*)\[.*?\](\s*\n\};)",lambda m:m.group(1)+gj+m.group(2),html,count=1,flags=re.DOTALL)
    if fecha:html=re.sub(r"In-House HOY [^\n<]{5,30}","In-House HOY - "+fecha,html)
    ts=datetime.now().strftime("%Y%m%d%H%M%S")
    html=re.sub(r"<!-- (adm|ts)[^>]*-->","<!-- ts:"+ts+" -->",html,count=1)
    if "<!-- ts:"+ts+" -->" not in html:html=html.replace("</head>","<!-- ts:"+ts+" --></head>",1)
    return html

def gh_deploy(html,token,user,repo):
    BASE="https://api.github.com/repos/"+user+"/"+repo
    H={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json","User-Agent":"ADM/1.0"}
    b64=base64.b64encode(html.encode("utf-8","replace")).decode()
    sha=None
    try:
        req=urllib.request.Request(BASE+"/contents/index.html",headers=H)
        with urllib.request.urlopen(req,timeout=15) as r:sha=json.loads(r.read()).get("sha")
    except:pass
    p={"message":"ADM "+datetime.now().strftime("%d/%m %H:%M"),"content":b64,"branch":"main"}
    if sha:p["sha"]=sha
    req=urllib.request.Request(BASE+"/contents/index.html",data=json.dumps(p).encode(),headers=H,method="PUT")
    with urllib.request.urlopen(req,timeout=60) as r:return json.loads(r.read()).get("commit",{}).get("sha","")[:8]

class Handler(BaseHTTPRequestHandler):
    def log_message(self,f,*a):print("["+datetime.now().strftime("%H:%M:%S")+"] "+str(f%a))
    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","POST,GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
    def do_OPTIONS(self):self.send_response(200);self.send_cors();self.end_headers()
    def do_GET(self):
        if self.path=="/health":
            self.send_response(200);self.send_header("Content-Type","application/json");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps({"status":"ok","version":"4-two-step","claude":bool(ANTHROPIC_KEY),"github":bool(GITHUB_TOKEN)}).encode())
        else:self.send_response(404);self.end_headers()
    def do_POST(self):
        if self.path!="/upload":self.send_response(404);self.end_headers();return
        try:
            ct=self.headers.get("Content-Type","")
            cl=int(self.headers.get("Content-Length",0))
            body=self.rfile.read(cl)
            pdf=None
            if "multipart" in ct:
                bd=ct.split("boundary=")[-1].strip().encode()
                for part in body.split(b"--"+bd):
                    if b"filename" in part:
                        idx=part.find(b"\r\n\r\n")
                        if idx!=-1:pdf=part[idx+4:].rstrip(b"\r\n--");break
            else:pdf=body
            if not pdf or len(pdf)<100:raise ValueError("PDF invalido")
            if not ANTHROPIC_KEY:raise ValueError("ANTHROPIC_API_KEY no configurada")
            data=extract_with_claude(pdf)
            guests=build_guests(data.get("guests",[]))
            print("Built: "+str(len(guests))+" guests")
            BASE2="https://api.github.com/repos/"+GITHUB_USER+"/"+GITHUB_REPO
            H2={"Authorization":"Bearer "+GITHUB_TOKEN,"Accept":"application/vnd.github+json","User-Agent":"ADM/1.0"}
            req=urllib.request.Request(BASE2+"/contents/index.html",headers=H2)
            with urllib.request.urlopen(req,timeout=15) as r:fd=json.loads(r.read())
            html=base64.b64decode(fd["content"]).decode("utf-8",errors="replace")
            html2=inject_html(html,data,guests)
            commit=gh_deploy(html2,GITHUB_TOKEN,GITHUB_USER,GITHUB_REPO)
            resp={"success":True,"fecha":data.get("fecha",""),"guests":len(guests),"dietary":sum(1 for g in guests if g["dietary"]),"checkins":sum(1 for g in guests if g["checkin"]),"checkouts":sum(1 for g in guests if g["checkout"]),"occ":data.get("pct_ocupacion",0),"commit":commit,"url":"https://"+GITHUB_REPO+".netlify.app","message":"OK "+str(len(guests))+" huespedes actualizando en 20s"}
            self.send_response(200);self.send_header("Content-Type","application/json;charset=utf-8");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps(resp,ensure_ascii=True).encode())
        except Exception as e:
            print("ERROR:"+str(e))
            self.send_response(500);self.send_header("Content-Type","application/json");self.send_cors();self.end_headers()
            self.wfile.write(json.dumps({"success":False,"error":str(e)}).encode())

if __name__=="__main__":
    print("ADM v4 Two-Step Port "+str(PORT))
    HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
